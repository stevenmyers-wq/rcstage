# webapp/visualiser/utils.py
import time
from webapp.rc_api import rc_api_call


MAX_EDGE_LABEL = 40

def _truncate_edge(text):
    if not text:
        return ""
    text = str(text).strip()
    if len(text) > MAX_EDGE_LABEL:
        return text[:MAX_EDGE_LABEL - 1] + "…"
    return text


class CallFlowTracer:
    def __init__(self):
        self.extension_cache = {}
        self.schedule_cache = {}
        self.request_logs = []
        self.nodes = []
        self.edges = []
        self.node_map = {}
        self.node_counter = 0
        self.ext_num_map = {}
        self.visited = set()

    # ------------------------------------------------------------------
    # API helpers
    # ------------------------------------------------------------------

    def api(self, endpoint):
        start = time.time()
        status = "SUCCESS"
        code = 200
        detail = ""
        try:
            should_bust = (
                "answering-rule" in endpoint or
                ("/call-queues/" in endpoint and "overflow-settings" not in endpoint)
            )
            sep = "&" if "?" in endpoint else "?"
            final_url = endpoint + f"{sep}_={int(time.time())}" if should_bust else endpoint

            resp = rc_api_call(final_url)
            duration = round((time.time() - start) * 1000, 2)

            if resp is None:
                status = "EMPTY"
                code = 0
            elif isinstance(resp, dict) and "errorCode" in resp:
                status = "ERROR"
                code = resp.get("errorCode", "?")
                detail = resp.get("message", "")

            self.request_logs.append({
                "method": "GET",
                "endpoint": final_url,
                "status": status,
                "code": str(code),
                "duration": f"{duration}ms",
                "detail": detail,
            })
            return resp
        except Exception as e:
            self.request_logs.append({
                "method": "GET",
                "endpoint": endpoint,
                "status": "EXCEPTION",
                "code": "0",
                "duration": "0ms",
                "detail": str(e),
            })
            return None

    def get_ext_info(self, ext_id):
        ext_id = str(ext_id)
        if ext_id in self.extension_cache:
            return self.extension_cache[ext_id]
        for _ in range(3):
            info = self.api(f"/restapi/v1.0/account/~/extension/{ext_id}")
            if info and "errorCode" not in info:
                self.extension_cache[ext_id] = info
                num = str(info.get("extensionNumber", ""))
                if num:
                    self.ext_num_map[num] = ext_id
                return info
            if info and info.get("errorCode") in ["CMN-102", "OGE-101"]:
                return {"type": "Unknown", "name": "Deleted Extension",
                        "extensionNumber": "???"}
            time.sleep(0.1)
        return None

    def resolve_ext_number(self, num):
        s = str(num)
        if s in self.ext_num_map:
            return self.ext_num_map[s]
        info = self.api(f"/restapi/v1.0/account/~/extension/{s}")
        if info and info.get("id"):
            eid = str(info["id"])
            self.extension_cache[eid] = info
            self.ext_num_map[s] = eid
            return eid
        return None

    def clean(self, text):
        if not text:
            return ""
        return str(text).replace('"', "'").replace("\n", " ").strip()

    # ------------------------------------------------------------------
    # Schedule helpers
    # ------------------------------------------------------------------

    def parse_schedule(self, schedule_obj):
        if not schedule_obj:
            return "24/7"
        try:
            lines = []
            if schedule_obj.get("weeklyRanges"):
                wr = schedule_obj["weeklyRanges"]
                time_map = {}
                items = []
                if isinstance(wr, dict):
                    for day, periods in wr.items():
                        if isinstance(periods, dict):
                            periods = [periods]
                        for p in periods:
                            items.append({"day": day, "from": p.get("from"), "to": p.get("to")})
                elif isinstance(wr, list):
                    for item in wr:
                        items.append({"day": item.get("dayOfWeek", "?"), "from": item.get("from"), "to": item.get("to")})

                days_order = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
                for item in items:
                    s = str(item.get("from") or "00:00").split(":")
                    e = str(item.get("to") or "23:59").split(":")
                    tk = f"{s[0]}:{s[1]}-{e[0]}:{e[1]}"
                    day_raw = item.get("day", "?")
                    day_short = day_raw[:1].upper() + day_raw[1:3].lower()
                    time_map.setdefault(tk, []).append(day_short)

                for tk, days in time_map.items():
                    days.sort(key=lambda d: days_order.index(d) if d in days_order else 99)
                    if len(days) == 7:
                        d_label = "Everyday"
                    elif len(days) == 5 and "Mon" in days and "Fri" in days:
                        d_label = "Mon-Fri"
                    elif len(days) == 2 and "Sat" in days and "Sun" in days:
                        d_label = "Weekends"
                    else:
                        d_label = ", ".join(days)
                    lines.append(f"{d_label}: {tk}")

            if schedule_obj.get("ranges"):
                for r in schedule_obj["ranges"]:
                    f = str(r.get("from", "")).replace("T", " ")[:16]
                    t = str(r.get("to", "")).replace("T", " ")[:16]
                    lines.append(f"{f} → {t}")

            return "\n".join(lines) if lines else "24/7"
        except Exception:
            return "Schedule unavailable"

    def get_biz_hours(self, ext_id):
        if ext_id in self.schedule_cache:
            return self.schedule_cache[ext_id]
        resp = self.api(f"/restapi/v1.0/account/~/extension/{ext_id}/business-hours")
        result = self.parse_schedule(resp.get("schedule") if resp else None)
        self.schedule_cache[ext_id] = result
        return result

    # ------------------------------------------------------------------
    # Graph helpers
    # ------------------------------------------------------------------

    def _next_id(self):
        nid = f"n{self.node_counter}"
        self.node_counter += 1
        return nid

    def add_node(self, nid, label, node_type, sublabel="", tooltip=""):
        self.nodes.append({
            "data": {
                "id": nid,
                "label": label,
                "type": node_type,
                "sublabel": sublabel,
                "tooltip": tooltip,
            }
        })

    def add_edge(self, source, target, label=""):
        self.edges.append({
            "data": {
                "source": source,
                "target": target,
                "label": _truncate_edge(label),
            }
        })

    def extract_target(self, obj):
        if not obj:
            return None
        if isinstance(obj, list):
            for item in obj:
                t = self.extract_target(item)
                if t:
                    return t
        if isinstance(obj, dict):
            ext = obj.get("extension")
            if ext:
                if ext.get("id"):
                    return str(ext["id"])
                if ext.get("extensionNumber"):
                    return self.resolve_ext_number(ext["extensionNumber"])
            if obj.get("phoneNumber"):
                return f"ext_{obj['phoneNumber']}"
        return None

    def _is_voicemail_action(self, action_str):
        if not action_str:
            return False
        return action_str in (
            "TakeMessagesOnly", "Voicemail", "voicemail",
            "SendToVoicemail", "sendToVoiceMail",
        )

    # ------------------------------------------------------------------
    # Account-level answering rules (main site / company number routing)
    # ------------------------------------------------------------------

    def _trace_account_rules(self, phone_nid, called_number=None):
        """
        Fetches and traces account-level answering rules for a company number.

        When called_number is provided (tracing from a specific DID):
          - Custom rules are only shown if their calledNumbers includes this DID
          - Business hours and after hours rules always apply (they are fallbacks)
          - Unmatched custom rules are silently skipped

        When called_number is None (tracing the main site directly):
          - All rules are shown regardless

        Rule structure differs from extension rules:
          - callHandlingAction: 'Bypass' | 'Operator' | 'Disconnect'
          - 'Bypass' → destination is rule['extension']['id']
        """
        rules_list = self.api("/restapi/v1.0/account/~/answering-rule?perPage=100")
        if not rules_list or not rules_list.get("records"):
            return

        inactive_labels = []

        # Normalise the called number for matching (strip + prefix variations)
        norm_called = called_number.lstrip("+") if called_number else None

        for rule_stub in rules_list["records"]:
            rule_id = rule_stub.get("id")
            if not rule_id:
                continue

            rule = self.api(f"/restapi/v1.0/account/~/answering-rule/{rule_id}")
            if not rule:
                continue

            r_type = rule.get("type", "Custom")
            is_active = rule.get("enabled", True)
            r_name = self.clean(rule.get("name", r_type))

            # When tracing from a specific company number, filter custom rules
            # to only those that match this DID via calledNumbers.
            # Business hours and after hours always apply — skip this filter for them.
            if called_number and r_type == "Custom":
                called_nums = rule.get("calledNumbers", [])
                matched = False
                for cn in called_nums:
                    cn_num = cn.get("phoneNumber", "").lstrip("+")
                    if norm_called and (cn_num == norm_called or
                                        cn_num in called_number or
                                        called_number in cn.get("phoneNumber", "")):
                        matched = True
                        break
                if not matched:
                    # This custom rule doesn't apply to this number — skip entirely
                    continue

            if r_type == "BusinessHours":
                lbl = "Business Hours"
            elif r_type == "AfterHours":
                lbl = "After Hours"
            else:
                lbl = r_name[:28]

            action = rule.get("callHandlingAction", "")
            target = None

            if action == "Bypass":
                ext_obj = rule.get("extension")
                if ext_obj and ext_obj.get("id"):
                    target = str(ext_obj["id"])

            elif action == "Operator":
                ext_obj = rule.get("extension")
                if ext_obj and ext_obj.get("id"):
                    target = str(ext_obj["id"])
                    lbl = f"{lbl} (Operator)"

            if not is_active:
                if target:
                    dest_info = self.get_ext_info(target)
                    dest_name = self.clean(
                        dest_info.get("name", target) if dest_info else target
                    )
                    inactive_labels.append(f"{lbl} → {dest_name}")
                else:
                    inactive_labels.append(f"{lbl} ({action or 'no destination'})")
                continue

            if target:
                self.trace(target, phone_nid, lbl)

        if inactive_labels:
            for node in self.nodes:
                if node["data"]["id"] == phone_nid:
                    existing = node["data"].get("tooltip", "")
                    extra = "Inactive Rules:\n" + "\n".join(inactive_labels)
                    node["data"]["tooltip"] = (
                        (existing + "\n\n" + extra).strip() if existing else extra
                    )
                    break

    # ------------------------------------------------------------------
    # Queue Type 1 overflow resolution
    # ------------------------------------------------------------------

    def _resolve_queue_overflow(self, ext_id, bh_rule, wait_time):
        dest_conditions = {}

        def _add(t_id, condition):
            if not t_id:
                return
            if t_id not in dest_conditions:
                dest_conditions[t_id] = []
            if condition not in dest_conditions[t_id]:
                dest_conditions[t_id].append(condition)

        if bh_rule:
            q_obj = bh_rule.get("queue") or {}

            hte_action = q_obj.get("holdTimeExpirationAction", "")
            if self._is_voicemail_action(hte_action):
                cond = f"Max wait >{wait_time}s" if wait_time else "Max wait"
                _add(f"vm_{ext_id}", cond)
            elif hte_action in ("TransferToExtension", "TransferToQueue", "TransferTo"):
                t = self.extract_target(
                    q_obj.get("holdTimeExpirationDestination") or
                    q_obj.get("transferToExtension") or
                    q_obj.get("transfer")
                )
                if t and t != ext_id:
                    cond = f"Max wait >{wait_time}s" if wait_time else "Max wait"
                    _add(t, cond)

            mc_action = q_obj.get("maxCallersAction", "")
            if self._is_voicemail_action(mc_action):
                _add(f"vm_{ext_id}", "Queue Full")
            elif mc_action in ("TransferToExtension", "TransferToQueue", "TransferTo"):
                t = self.extract_target(
                    q_obj.get("maxCallersDestination") or
                    q_obj.get("transferToExtension")
                )
                if t and t != ext_id:
                    _add(t, "Queue Full")

            bh_action = bh_rule.get("callHandlingAction", "")
            if self._is_voicemail_action(bh_action) and not dest_conditions:
                cond = f"Max wait >{wait_time}s" if wait_time else "Voicemail"
                _add(f"vm_{ext_id}", cond)

            t = self.extract_target(bh_rule.get("transfer"))
            if t and t != ext_id and not dest_conditions:
                _add(t, "Overflow")

            t = self.extract_target(bh_rule.get("unconditionalForwarding"))
            if t and t != ext_id and not dest_conditions:
                _add(t, "Overflow")

        result = []
        for t_id, conditions in dest_conditions.items():
            label = " & ".join(conditions)
            result.append((t_id, label))

        return result

    # ------------------------------------------------------------------
    # Core tracer
    # ------------------------------------------------------------------

    def trace(self, ext_id, parent_nid=None, edge_label="", history=None):
        if history is None:
            history = []

        ext_id = str(ext_id)

        if ext_id.startswith("ext_"):
            nid = self._next_id()
            number = ext_id.replace("ext_", "")
            self.add_node(nid, number, "external", sublabel="External Transfer")
            if parent_nid:
                self.add_edge(parent_nid, nid, edge_label)
            return nid

        if ext_id.startswith("vm_"):
            nid = self._next_id()
            self.add_node(nid, "Voicemail", "voicemail")
            if parent_nid:
                self.add_edge(parent_nid, nid, edge_label)
            return nid

        if ext_id in history:
            if ext_id in self.node_map and parent_nid:
                self.add_edge(parent_nid, self.node_map[ext_id], edge_label + " ↩")
            return self.node_map.get(ext_id)

        if ext_id in self.node_map:
            if parent_nid:
                self.add_edge(parent_nid, self.node_map[ext_id], edge_label)
            return self.node_map[ext_id]

        self.visited.add(ext_id)
        new_history = history + [ext_id]

        info = self.get_ext_info(ext_id)
        if not info:
            nid = self._next_id()
            self.node_map[ext_id] = nid
            self.add_node(nid, "Unknown", "unknown", sublabel=f"ID: {ext_id}")
            if parent_nid:
                self.add_edge(parent_nid, nid, edge_label)
            return nid

        e_type = info.get("type", "Unknown")
        name = self.clean(info.get("name", "Unknown"))
        ext_num = str(info.get("extensionNumber", ""))

        if e_type == "Department":
            q_check = self.api(f"/restapi/v1.0/account/~/call-queues/{ext_id}")
            if q_check and "errorCode" not in q_check:
                e_type = "CallQueue"

        node_type_map = {
            "IvrMenu": "ivr",
            "CallQueue": "queue",
            "Department": "queue",
            "AnnouncementOnly": "autoreceptionist",
            "Site": "site",
            "User": "user",
            "DigitalUser": "user",
            "VirtualUser": "user",
            "FlexibleUser": "user",
            "Limited": "user",
        }
        node_type = node_type_map.get(e_type, "user")

        nid = self._next_id()
        self.node_map[ext_id] = nid

        # ---------------------------------------------------------------
        # CALL QUEUE
        # ---------------------------------------------------------------
        if e_type in ("CallQueue", "Department"):
            member_names = []
            member_count = 0
            tooltip_parts = []

            m_resp = self.api(f"/restapi/v1.0/account/~/call-queues/{ext_id}/members")
            if m_resp and m_resp.get("records"):
                records = m_resp["records"]
                member_count = len(records)
                for m in records:
                    mi = self.get_ext_info(m["id"])
                    if mi:
                        member_names.append(
                            f"{self.clean(mi.get('name', '?'))} "
                            f"x{mi.get('extensionNumber', '?')}"
                        )

            bh_rule = self.api(
                f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule/business-hours-rule"
            )
            wait_time = None
            max_callers = None
            if bh_rule:
                q_obj = bh_rule.get("queue") or {}
                wait_time = q_obj.get("holdTime")
                max_callers = q_obj.get("maxCallers")

            overflow_targets = self._resolve_queue_overflow(ext_id, bh_rule, wait_time)

            type2_overflow_names = []
            ov_resp = self.api(
                f"/restapi/v1.0/account/~/extension/{ext_id}/overflow-settings"
            )
            if ov_resp and ov_resp.get("enabled") and ov_resp.get("items"):
                for item in ov_resp["items"]:
                    ov_name = self.clean(item.get("name", "?"))
                    ov_ext = str(item.get("extensionNumber", ""))
                    type2_overflow_names.append(f"{ov_name} x{ov_ext}")

            sched = self.get_biz_hours(ext_id)

            display_members = member_names[:4]
            member_label = ""
            if display_members:
                member_label = "\n" + "\n".join(display_members)
                if member_count > 4:
                    member_label += f"\n+{member_count - 4} more"
            if type2_overflow_names:
                member_label += "\n─────────────"
                member_label += "\n↪ " + "\n↪ ".join(type2_overflow_names)

            tooltip_parts.append(f"Ext {ext_num} · Call Queue")
            if wait_time is not None:
                tooltip_parts.append(f"Max wait: {wait_time}s")
            if max_callers is not None:
                tooltip_parts.append(f"Max callers: {max_callers}")
            if member_names:
                tooltip_parts.append("Members:\n" + "\n".join(member_names))
            if type2_overflow_names:
                tooltip_parts.append(
                    "Overflow Queues (backup agents):\n" +
                    "\n".join(type2_overflow_names)
                )
            if sched and sched != "24/7":
                tooltip_parts.append(f"Hours:\n{sched}")

            self.add_node(
                nid,
                name + member_label,
                node_type,
                sublabel="",
                tooltip="\n\n".join(tooltip_parts),
            )
            if parent_nid:
                self.add_edge(parent_nid, nid, edge_label)

            seen = set()
            for t_id, lbl in overflow_targets:
                if t_id not in seen:
                    self.trace(t_id, nid, lbl, new_history)
                    seen.add(t_id)

            self._trace_rules(ext_id, nid, new_history,
                              skip_bh=True, active_only=True)

        # ---------------------------------------------------------------
        # IVR MENU
        # ---------------------------------------------------------------
        elif e_type == "IvrMenu":
            ivr = self.api(f"/restapi/v1.0/account/~/ivr-menus/{ext_id}")

            key_lines = []
            actions = []
            if ivr and ivr.get("actions"):
                for act in ivr["actions"]:
                    key = act.get("input", "?")
                    t = self.extract_target(act)
                    if t:
                        actions.append((key, t))
                        dest_info = self.get_ext_info(t) if not t.startswith("ext_") else None
                        dest_name = self.clean(dest_info.get("name", t) if dest_info else t)
                        key_lines.append(f"[{key}] {dest_name[:25]}")

            key_label = ("\n" + "\n".join(key_lines)) if key_lines else ""

            default_t = None
            if ivr:
                default_t = self.extract_target(ivr.get("defaultAction"))
                if default_t:
                    key_label += "\n[Timeout] →"

            self.add_node(nid, name + key_label, node_type,
                          sublabel=f"IVR · Ext {ext_num}", tooltip="")
            if parent_nid:
                self.add_edge(parent_nid, nid, edge_label)

            for key, t_id in actions:
                self.trace(t_id, nid, f"Press {key}", new_history)
            if default_t:
                self.trace(default_t, nid, "Timeout", new_history)

        # ---------------------------------------------------------------
        # AUTO RECEPTIONIST
        # ---------------------------------------------------------------
        elif e_type == "AnnouncementOnly":
            sched = self.get_biz_hours(ext_id)
            self.add_node(nid, name, "autoreceptionist",
                          sublabel=f"Auto Receptionist · Ext {ext_num}",
                          tooltip=f"Hours:\n{sched}" if sched != "24/7" else "")
            if parent_nid:
                self.add_edge(parent_nid, nid, edge_label)
            self._trace_rules(ext_id, nid, new_history,
                              skip_bh=False, active_only=True)

        # ---------------------------------------------------------------
        # SITE
        # ---------------------------------------------------------------
        elif e_type == "Site":
            self.add_node(nid, name, "site", sublabel=f"Site · Ext {ext_num}")
            if parent_nid:
                self.add_edge(parent_nid, nid, edge_label)
            self._trace_rules(ext_id, nid, new_history,
                              skip_bh=False, active_only=True)

        # ---------------------------------------------------------------
        # USER / everything else
        # ---------------------------------------------------------------
        else:
            self.add_node(nid, name, node_type, sublabel=f"Ext {ext_num}")
            if parent_nid:
                self.add_edge(parent_nid, nid, edge_label)
            self._trace_rules(ext_id, nid, new_history,
                              skip_bh=False, active_only=True)

        return nid

    # ------------------------------------------------------------------
    # Extension rule tracer
    # ------------------------------------------------------------------

    def _trace_rules(self, ext_id, nid, history,
                     skip_bh=False, active_only=False):
        rules_resp = self.api(
            f"/restapi/v1.0/account/~/extension/{ext_id}"
            f"/answering-rule?view=Detailed&showInactive=true"
        )
        if not rules_resp or not rules_resp.get("records"):
            return

        inactive_rule_lines = []

        for r in rules_resp["records"]:
            r_type = r.get("type")
            if skip_bh and r_type == "BusinessHours":
                continue

            is_active = r.get("enabled", True)

            if r_type == "AfterHours":
                lbl = "After Hours"
            elif r_type == "BusinessHours":
                lbl = "Business Hours"
            elif r_type == "Custom":
                lbl = self.clean(r.get("name", "Custom Rule"))[:28]
            else:
                lbl = self.clean(r.get("name", r_type or "Rule"))[:28]

            target = self.extract_target(r.get("transfer"))
            if not target:
                target = self.extract_target(r.get("unconditionalForwarding"))
            if not target and self._is_voicemail_action(r.get("callHandlingAction", "")):
                target = f"vm_{ext_id}"

            if not is_active and active_only:
                if target:
                    dest_label = target
                    if target.startswith("vm_"):
                        dest_label = "Voicemail"
                    elif target.startswith("ext_"):
                        dest_label = target.replace("ext_", "")
                    else:
                        dest_info = self.get_ext_info(target)
                        if dest_info:
                            dest_label = self.clean(dest_info.get("name", target))
                    inactive_rule_lines.append(f"{lbl} → {dest_label}")
                else:
                    inactive_rule_lines.append(f"{lbl} (no destination)")
                continue

            if target:
                edge_lbl = lbl if is_active else f"[Off] {lbl}"
                self.trace(target, nid, edge_lbl, history)

        if inactive_rule_lines:
            for node in self.nodes:
                if node["data"]["id"] == nid:
                    existing = node["data"].get("tooltip", "")
                    extra = "Inactive Rules:\n" + "\n".join(inactive_rule_lines)
                    node["data"]["tooltip"] = (
                        (existing + "\n\n" + extra).strip() if existing else extra
                    )
                    break

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def generate(self, start_ext_id):
        start_ext_id = str(start_ext_id)

        # Company number — route via account-level answering rules
        if start_ext_id.startswith("company_"):
            number = start_ext_id.replace("company_", "")
            phone_nid = self._next_id()
            self.add_node(phone_nid, number, "phone",
                          sublabel="Company Number",
                          tooltip="Routes via account-level answering rules")
            self._trace_account_rules(phone_nid, called_number=number)
            return ({"nodes": self.nodes, "edges": self.edges},
                    self.request_logs)

        # Direct number — look up assigned extension and trace
        if start_ext_id.startswith("ext_"):
            number = start_ext_id.replace("ext_", "")
            phones_resp = self.api(
                f"/restapi/v1.0/account/~/phone-number?phoneNumber={number}"
            )
            if phones_resp and phones_resp.get("records"):
                for p in phones_resp["records"]:
                    assigned = p.get("extension", {})
                    if assigned.get("id"):
                        phone_nid = self._next_id()
                        self.add_node(phone_nid, number, "phone",
                                      sublabel="Inbound Number")
                        self.trace(str(assigned["id"]), phone_nid, "Routes to")
                        return ({"nodes": self.nodes, "edges": self.edges},
                                self.request_logs)

            nid = self._next_id()
            self.add_node(nid, number, "phone", sublabel="Unassigned / Not Found")
            return {"nodes": self.nodes, "edges": self.edges}, self.request_logs

        # Normal extension trace
        self.trace(start_ext_id)
        return {"nodes": self.nodes, "edges": self.edges}, self.request_logs


def generate_graph_flow(start_ext_id):
    tracer = CallFlowTracer()
    return tracer.generate(start_ext_id)
