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
        self.phone_map = None          # ext_id -> [{"number", "usage"}]
        self.entry_node_ids = []       # node ids that are graph entry points
        self.show_inactive = False     # also draw disabled/inactive rules
        self.notif_cache = {}          # ext_id -> voicemail notification email(s)
        self._live_state_cache = {}    # (ext_id, skip_bh) -> {ruleType: {target, enabled}}

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
    # Direct-number enrichment
    # ------------------------------------------------------------------

    def _load_phone_map(self):
        """Lazily fetch the account phone-number inventory once and index it
        by the extension id it's assigned to. Best-effort — a failure just
        means nodes render without their direct numbers."""
        if self.phone_map is not None:
            return self.phone_map
        self.phone_map = {}
        page = 1
        try:
            while page <= 40:  # safety cap (~10k numbers)
                resp = self.api(
                    f"/restapi/v1.0/account/~/phone-number?perPage=250&page={page}"
                )
                if not resp or not resp.get("records"):
                    break
                for p in resp["records"]:
                    ext_id = str((p.get("extension") or {}).get("id", "") or "")
                    if not ext_id or ext_id == "None":
                        continue
                    self.phone_map.setdefault(ext_id, []).append({
                        "number": p.get("phoneNumber", ""),
                        "usage": p.get("usageType", ""),
                    })
                nav = resp.get("navigation", {}) or {}
                if nav.get("nextPage"):
                    page += 1
                else:
                    break
        except Exception:
            pass
        return self.phone_map

    def _direct_numbers(self, ext_id, limit=4):
        pm = self._load_phone_map()
        entries = pm.get(str(ext_id), [])
        nums = [e["number"] for e in entries if e.get("number")]
        if not nums:
            return ""
        shown = nums[:limit]
        extra = len(nums) - len(shown)
        line = ", ".join(shown)
        if extra > 0:
            line += f" +{extra} more"
        return line

    def _vm_notification(self, ext_id):
        """Return the voicemail *notification* email address(es) for an
        extension. This can differ from the base contact email, so it's worth
        surfacing on voicemail nodes. Best-effort + cached."""
        ext_id = str(ext_id)
        if ext_id in self.notif_cache:
            return self.notif_cache[ext_id]
        result = ""
        try:
            resp = self.api(
                f"/restapi/v1.0/account/~/extension/{ext_id}/notification-settings"
            )
            if resp and "errorCode" not in resp:
                vm = resp.get("voicemails") or {}
                emails = []
                if resp.get("advancedMode"):
                    emails = vm.get("advancedEmailAddresses") or []
                if not emails:
                    emails = resp.get("emailAddresses") or []
                emails = [e for e in emails if e]
                # de-dupe, preserve order
                result = ", ".join(dict.fromkeys(emails))
        except Exception:
            result = ""
        self.notif_cache[ext_id] = result
        return result

    @staticmethod
    def _fmt_secs(val):
        if val is None or val == "":
            return None
        try:
            s = int(val)
        except (TypeError, ValueError):
            return str(val)
        if s < 60:
            return f"{s}s"
        m, sec = divmod(s, 60)
        return f"{m}m {sec}s" if sec else f"{m}m"

    _RING_TYPE_MAP = {
        "FixedOrder": "Sequential (fixed order)",
        "Rotating": "Rotating",
        "Simultaneous": "Simultaneous",
    }

    _ACTION_LABELS = {
        "TakeMessagesOnly": "Send to Voicemail",
        "Voicemail": "Send to Voicemail",
        "ForwardToExtension": "Forward to Extension",
        "TransferToExtension": "Transfer to Extension",
        "TransferToQueue": "Transfer to Queue",
        "UnconditionalForwarding": "Unconditional Forward",
        "Disconnect": "Disconnect",
        "PlayAnnouncementOnly": "Play Announcement",
    }

    def _human_action(self, action):
        if not action:
            return None
        return self._ACTION_LABELS.get(action, action)

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

    def add_edge(self, source, target, label="", disabled=False):
        data = {
            "source": source,
            "target": target,
            "label": _truncate_edge(label),
        }
        if disabled:
            data["disabled"] = True
        self.edges.append({"data": data})

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
                    if self.show_inactive:
                        self.trace(target, phone_nid, f"[Off] {lbl}", disabled=True)
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

    def trace(self, ext_id, parent_nid=None, edge_label="", history=None, disabled=False):
        if history is None:
            history = []

        ext_id = str(ext_id)

        if ext_id.startswith("ext_"):
            nid = self._next_id()
            number = ext_id.replace("ext_", "")
            self.add_node(nid, number, "external", sublabel="External Transfer")
            if parent_nid:
                self.add_edge(parent_nid, nid, edge_label, disabled=disabled)
            return nid

        if ext_id.startswith("vm_"):
            nid = self._next_id()
            owner = ext_id.replace("vm_", "")
            owner_info = self.get_ext_info(owner) if owner else None
            owner_name = self.clean(owner_info.get("name", "")) if owner_info else ""
            vm_overview = ["Overview", "Type: Voicemail"]
            if owner_name:
                vm_overview.append(f"Mailbox: {owner_name}")
            notif = self._vm_notification(owner)
            if notif:
                vm_overview.append(f"VM notification: {notif}")
            self.add_node(nid, "Voicemail", "voicemail",
                          sublabel=owner_name,
                          tooltip="\n".join(vm_overview) if len(vm_overview) > 2 else "")
            if parent_nid:
                self.add_edge(parent_nid, nid, edge_label, disabled=disabled)
            return nid

        if ext_id.startswith("announce_"):
            # A "play announcement (and disconnect)" terminating action — surfaced by
            # the v2 call-handling read. Drawn as its own leaf so it's clearly not a
            # transfer to an extension.
            nid = self._next_id()
            self.add_node(nid, "Announcement", "autoreceptionist",
                          sublabel="Play Announcement",
                          tooltip="Overview\nType: Announcement (play & disconnect)")
            if parent_nid:
                self.add_edge(parent_nid, nid, edge_label, disabled=disabled)
            return nid

        if ext_id in history:
            if ext_id in self.node_map and parent_nid:
                self.add_edge(parent_nid, self.node_map[ext_id], edge_label + " ↩", disabled=disabled)
            return self.node_map.get(ext_id)

        if ext_id in self.node_map:
            if parent_nid:
                self.add_edge(parent_nid, self.node_map[ext_id], edge_label, disabled=disabled)
            return self.node_map[ext_id]

        self.visited.add(ext_id)
        new_history = history + [ext_id]

        info = self.get_ext_info(ext_id)
        if not info:
            nid = self._next_id()
            self.node_map[ext_id] = nid
            self.add_node(nid, "Unknown", "unknown", sublabel=f"ID: {ext_id}")
            if parent_nid:
                self.add_edge(parent_nid, nid, edge_label, disabled=disabled)
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
            ring_type = None
            agent_ring = None
            wrap_up = None
            full_action = None
            expire_action = None
            if bh_rule:
                q_obj = bh_rule.get("queue") or {}
                wait_time = q_obj.get("holdTime")
                max_callers = q_obj.get("maxCallers")
                ring_type = self._RING_TYPE_MAP.get(
                    q_obj.get("transferMode"), q_obj.get("transferMode")
                )
                agent_ring = q_obj.get("agentTimeout")
                wrap_up = q_obj.get("wrapUpTime")
                full_action = self._human_action(q_obj.get("maxCallersAction"))
                expire_action = self._human_action(q_obj.get("holdTimeExpirationAction"))

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

            q_status = self.clean(info.get("status", ""))
            q_email = self.clean((info.get("contact") or {}).get("email", ""))
            direct_nums = self._direct_numbers(ext_id)

            # ── Overview section (key: value rows) ──
            overview = ["Overview"]
            overview.append(f"Extension: {ext_num}")
            overview.append("Type: Call Queue")
            if q_status:
                overview.append(f"Status: {q_status}")
            if ring_type:
                overview.append(f"Ring type: {ring_type}")
            if member_count:
                overview.append(f"Members: {member_count}")
            if direct_nums:
                overview.append(f"Direct numbers: {direct_nums}")
            if q_email:
                overview.append(f"Email: {q_email}")
            tooltip_parts.append("\n".join(overview))

            # ── Routing behaviour section ──
            routing = ["Routing"]
            if agent_ring is not None:
                routing.append(f"Agent ring time: {self._fmt_secs(agent_ring)}")
            if wait_time is not None:
                routing.append(f"Max wait in queue: {self._fmt_secs(wait_time)}")
            if wrap_up is not None:
                routing.append(f"Wrap-up time: {self._fmt_secs(wrap_up)}")
            if max_callers is not None:
                routing.append(f"Max callers: {max_callers}")
            if full_action:
                routing.append(f"When full: {full_action}")
            if expire_action:
                routing.append(f"When wait exceeded: {expire_action}")
            if len(routing) > 1:
                tooltip_parts.append("\n".join(routing))

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
                self.add_edge(parent_nid, nid, edge_label, disabled=disabled)

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

            ivr_overview = ["Overview", f"Extension: {ext_num}", "Type: IVR Menu"]
            if actions:
                ivr_overview.append(f"Key options: {len(actions)}")
            direct_nums = self._direct_numbers(ext_id)
            if direct_nums:
                ivr_overview.append(f"Direct numbers: {direct_nums}")
            prompt_mode = None
            if ivr and ivr.get("prompt"):
                pr = ivr["prompt"]
                prompt_mode = pr.get("mode") or ("Audio" if pr.get("audio") else None)
            if prompt_mode:
                ivr_overview.append(f"Greeting: {prompt_mode}")
            ivr_tooltip_parts = ["\n".join(ivr_overview)]
            if key_lines:
                ivr_tooltip_parts.append("Key Press Options:\n" + "\n".join(key_lines))

            self.add_node(nid, name + key_label, node_type,
                          sublabel=f"IVR · Ext {ext_num}",
                          tooltip="\n\n".join(ivr_tooltip_parts))
            if parent_nid:
                self.add_edge(parent_nid, nid, edge_label, disabled=disabled)

            for key, t_id in actions:
                self.trace(t_id, nid, f"Press {key}", new_history)
            if default_t:
                self.trace(default_t, nid, "Timeout", new_history)

        # ---------------------------------------------------------------
        # AUTO RECEPTIONIST
        # ---------------------------------------------------------------
        elif e_type == "AnnouncementOnly":
            sched = self.get_biz_hours(ext_id)
            ar_overview = ["Overview", f"Extension: {ext_num}",
                           "Type: Auto Receptionist"]
            direct_nums = self._direct_numbers(ext_id)
            if direct_nums:
                ar_overview.append(f"Direct numbers: {direct_nums}")
            ar_parts = ["\n".join(ar_overview)]
            if sched and sched != "24/7":
                ar_parts.append(f"Hours:\n{sched}")
            self.add_node(nid, name, "autoreceptionist",
                          sublabel=f"Auto Receptionist · Ext {ext_num}",
                          tooltip="\n\n".join(ar_parts))
            if parent_nid:
                self.add_edge(parent_nid, nid, edge_label, disabled=disabled)
            self._trace_rules(ext_id, nid, new_history,
                              skip_bh=False, active_only=True)

        # ---------------------------------------------------------------
        # SITE
        # ---------------------------------------------------------------
        elif e_type == "Site":
            sched = self.get_biz_hours(ext_id)
            site_overview = ["Overview", f"Extension: {ext_num}", "Type: Site"]
            op_ext = (info.get("operator") or {}).get("extensionNumber")
            if op_ext:
                site_overview.append(f"Operator ext: {op_ext}")
            site_parts = ["\n".join(site_overview)]
            if sched and sched != "24/7":
                site_parts.append(f"Hours:\n{sched}")
            self.add_node(nid, name, "site", sublabel=f"Site · Ext {ext_num}",
                          tooltip="\n\n".join(site_parts))
            if parent_nid:
                self.add_edge(parent_nid, nid, edge_label, disabled=disabled)
            self._trace_rules(ext_id, nid, new_history,
                              skip_bh=False, active_only=True)

        # ---------------------------------------------------------------
        # USER / everything else
        # ---------------------------------------------------------------
        else:
            u_overview = ["Overview", f"Extension: {ext_num}",
                          f"Type: {e_type}"]
            u_status = self.clean(info.get("status", ""))
            if u_status:
                u_overview.append(f"Status: {u_status}")
            contact = info.get("contact") or {}
            u_email = self.clean(contact.get("email", ""))
            if u_email:
                u_overview.append(f"Email: {u_email}")
            dept = self.clean(contact.get("department", ""))
            if dept:
                u_overview.append(f"Department: {dept}")
            site_obj = info.get("site") or {}
            site_name = self.clean(site_obj.get("name", ""))
            if site_name:
                u_overview.append(f"Site: {site_name}")
            direct_nums = self._direct_numbers(ext_id)
            if direct_nums:
                u_overview.append(f"Direct numbers: {direct_nums}")
            vm_notif = self._vm_notification(ext_id)
            if vm_notif and vm_notif != u_email:
                u_overview.append(f"VM notification: {vm_notif}")
            self.add_node(nid, name, node_type, sublabel=f"Ext {ext_num}",
                          tooltip="\n".join(u_overview))
            if parent_nid:
                self.add_edge(parent_nid, nid, edge_label, disabled=disabled)
            self._trace_rules(ext_id, nid, new_history,
                              skip_bh=False, active_only=True)

        return nid

    # ------------------------------------------------------------------
    # Extension rule tracer
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Live after-hours / business-hours routing
    # ------------------------------------------------------------------
    # The detailed answering-rule LIST endpoint used below is stale on New Call
    # Handling & Forwarding (CH&F) accounts. The per-state v1 SHORTCUT endpoints
    # (…/answering-rule/after-hours-rule) are live for SOME changes but not all —
    # e.g. an after-hours "play announcement" shows live, but a switch to the
    # queue's own voicemail can still read as an old transfer. The authoritative
    # source is the v2 comm-handling API, so we read that per state first and only
    # fall back to the v1 shortcut when v2 is unavailable/unparseable. Both results
    # are logged ("Live AH/BH: …") so the source of each state is visible.

    def _shortcut_rule_target(self, rule, ext_id):
        """Resolve a v1 answering-rule object (shortcut endpoint) to a tracer target
        id (extension id, ext_<number>, vm_<id>, announce_<id>), or None. Mirrors how
        the queue audit interprets callHandlingAction."""
        if not isinstance(rule, dict):
            return None
        action = rule.get("callHandlingAction", "")
        vm_obj = rule.get("voicemail") or {}
        if action == "PlayAnnouncementOnly":
            return f"announce_{ext_id}"
        if self._is_voicemail_action(action) or (vm_obj.get("enabled") and not action):
            rec = vm_obj.get("recipient") or {}
            recip = None
            if rec.get("id"):
                recip = str(rec["id"])
            elif rec.get("extensionNumber"):
                recip = self.resolve_ext_number(rec["extensionNumber"])
            return f"vm_{recip or ext_id}"
        target = self.extract_target(rule.get("transfer"))
        if not target:
            target = self.extract_target(rule.get("unconditionalForwarding"))
        if target == str(ext_id):
            target = f"vm_{ext_id}"
        return target

    def _v2_active_target(self, action):
        """Return the active *terminating* target dict for a v2 action, or None.
        Ringing targets (ring members) are ignored — we want the final destination."""
        targets = action.get("targets")
        if not isinstance(targets, list):
            return None
        ttt = action.get("terminatingTargetType") or action.get("activeTargetType")
        if ttt:
            for t in targets:
                if not isinstance(t, dict):
                    continue
                tt = str(t.get("type", ""))
                if tt == ttt or t.get("name") == ttt or tt.startswith(str(ttt)):
                    return t
        for t in targets:
            if isinstance(t, dict) and "Terminating" in str(t.get("type", "")):
                return t
        return None

    def _v2_rule_target(self, rule, ext_id):
        """Map a v2 comm-handling state rule to (target, matched). matched=True means
        the rule was understood (so v2 is authoritative even when target is None, e.g.
        Disconnect); matched=False means fall back to the v1 shortcut."""
        disp = rule.get("dispatching") or {}
        actions = disp.get("actions") or rule.get("actions") or []
        chosen = None
        if isinstance(actions, list):
            for a in actions:
                if isinstance(a, dict):
                    t = self._v2_active_target(a)
                    if t:
                        chosen = t  # last terminating action wins
        if not chosen:
            return (None, False)
        ttype = str(chosen.get("type", ""))
        if "Extension" in ttype:
            eid = str((chosen.get("extension") or {}).get("id", "") or "")
            return (eid or None, bool(eid))
        if "PhoneNumber" in ttype:
            num = str((chosen.get("destination") or {}).get("phoneNumber", "") or "")
            return (f"ext_{num}" if num else None, bool(num))
        if "VoiceMail" in ttype or "Voicemail" in ttype:
            rec = chosen.get("recipient") or chosen.get("extension") or {}
            rid = str(rec.get("id", "") or "")
            return (f"vm_{rid or ext_id}", True)
        if "PlayAnnouncement" in ttype:
            return (f"announce_{ext_id}", True)
        if "Disconnect" in ttype:
            return (None, True)
        return (None, False)

    @staticmethod
    def _v2_summary(rule):
        disp = rule.get("dispatching") or {}
        actions = disp.get("actions") or rule.get("actions") or []
        bits = []
        if isinstance(actions, list):
            for a in actions:
                if not isinstance(a, dict):
                    continue
                ttt = a.get("terminatingTargetType") or a.get("activeTargetType")
                types = [str(t.get("type")) for t in (a.get("targets") or [])
                         if isinstance(t, dict)]
                bits.append(f"{a.get('type')}[ttt={ttt};{types}]")
        return "|".join(bits) if bits else f"keys={list(rule.keys())}"

    def _live_state_targets(self, ext_id, skip_bh=False):
        """Return {ruleType: {'target','enabled'}} for AfterHours (and BusinessHours
        when not skip_bh). Prefer the v2 comm-handling state rule (authoritative on
        CH&F accounts); fall back to the v1 answering-rule shortcut when v2 is
        unavailable or unparseable."""
        ext_id = str(ext_id)
        cache_key = (ext_id, skip_bh)
        if cache_key in self._live_state_cache:
            return self._live_state_cache[cache_key]

        # (v2 state id, v1 shortcut rule id, result key)
        wanted = [("after-hours", "after-hours-rule", "AfterHours")]
        if not skip_bh:
            wanted.append(("work-hours", "business-hours-rule", "BusinessHours"))

        result = {}
        log_bits = []
        for v2_state, v1_rule, key in wanted:
            target = None
            enabled = True
            src = None

            # 1) v2 comm-handling (authoritative on CH&F accounts)
            v2 = self.api(
                f"/restapi/v2/accounts/~/extensions/{ext_id}"
                f"/comm-handling/voice/state-rules/{v2_state}"
            )
            if isinstance(v2, dict) and not v2.get("errorCode"):
                t, matched = self._v2_rule_target(v2, ext_id)
                if matched:
                    target = t
                    enabled = bool(v2.get("enabled", True))
                    src = "v2"
                    log_bits.append(f"{key}.v2->{target}")
                else:
                    log_bits.append(f"{key}.v2?{self._v2_summary(v2)}")
            else:
                code = v2.get("errorCode") if isinstance(v2, dict) else "EMPTY"
                log_bits.append(f"{key}.v2=ERR:{code}")

            # 2) v1 shortcut fallback
            if src is None:
                sc = self.api(
                    f"/restapi/v1.0/account/~/extension/{ext_id}/answering-rule/{v1_rule}"
                )
                if isinstance(sc, dict) and not sc.get("errorCode"):
                    target = self._shortcut_rule_target(sc, ext_id)
                    enabled = bool(sc.get("enabled", True))
                    src = "v1"
                    log_bits.append(f"{key}.v1={sc.get('callHandlingAction')}->{target}")
                else:
                    code = sc.get("errorCode") if isinstance(sc, dict) else "EMPTY"
                    log_bits.append(f"{key}.v1=ERR:{code}")

            if src is not None:
                result[key] = {"target": target, "enabled": enabled}

        # NOTE: the debug panel only renders `detail` for non-SUCCESS rows, so put
        # the summary in `endpoint` (always shown) to keep it visible.
        summary = ', '.join(log_bits) if log_bits else 'none'
        self.request_logs.append({
            "method": "GET",
            "endpoint": f"Live AH/BH {ext_id}: {summary}",
            "status": "SUCCESS", "code": "200", "duration": "0ms",
            "detail": summary,
        })
        self._live_state_cache[cache_key] = result
        return result

    def _trace_rules(self, ext_id, nid, history,
                     skip_bh=False, active_only=False):
        rules_resp = self.api(
            f"/restapi/v1.0/account/~/extension/{ext_id}"
            f"/answering-rule?view=Detailed&showInactive=true"
        )
        # The detailed list above can be stale for the default states; the shortcut
        # endpoints carry the live routing and override the list target below.
        live_states = self._live_state_targets(ext_id, skip_bh)

        if not rules_resp or not rules_resp.get("records"):
            # No rules in the list, but the shortcuts may still have states to draw.
            if live_states:
                self._trace_states_only(ext_id, nid, history, live_states, skip_bh, active_only)
            return

        inactive_rule_lines = []
        live_seen = set()

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

            action = r.get("callHandlingAction", "")
            vm_obj = r.get("voicemail") or {}

            # A "send to voicemail" rule → draw the recipient's Voicemail box
            # (the recipient can be another extension, e.g. the call queue's own
            # voicemail) instead of resolving a self-transfer into a loop back.
            if self._is_voicemail_action(action) or (vm_obj.get("enabled") and not action):
                rec = vm_obj.get("recipient") or {}
                recip = None
                if rec.get("id"):
                    recip = str(rec["id"])
                elif rec.get("extensionNumber"):
                    recip = self.resolve_ext_number(rec["extensionNumber"])
                target = f"vm_{recip or ext_id}"
            else:
                target = self.extract_target(r.get("transfer"))
                if not target:
                    target = self.extract_target(r.get("unconditionalForwarding"))
                # A rule that "transfers" to its own extension really means VM.
                if target == str(ext_id):
                    target = f"vm_{ext_id}"

            # Live override: replace the (possibly stale) list target for the default
            # states with what the shortcut endpoint reports — the same source the
            # queue audit trusts. The shortcut is authoritative for these states, so
            # override whenever it returned the state (a None target means no onward
            # branch, e.g. Disconnect), never leaving the stale list value in place.
            if r_type in live_states:
                target = live_states[r_type].get("target")
                is_active = live_states[r_type].get("enabled", is_active)
                live_seen.add(r_type)

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
                    # Optionally draw the disabled rule as a dashed branch too.
                    if self.show_inactive:
                        self.trace(target, nid, f"[Off] {lbl}", history, disabled=True)
                else:
                    inactive_rule_lines.append(f"{lbl} (no destination)")
                continue

            if target:
                edge_lbl = lbl if is_active else f"[Off] {lbl}"
                self.trace(target, nid, edge_lbl, history,
                           disabled=not is_active)

        # Draw any live states the list didn't carry (e.g. an after-hours rule the
        # list view omits).
        for r_type, info in live_states.items():
            if r_type in live_seen:
                continue
            if skip_bh and r_type == "BusinessHours":
                continue
            target = info.get("target")
            if not target:
                continue
            is_active = info.get("enabled", True)
            lbl = "After Hours" if r_type == "AfterHours" else "Business Hours"
            if not is_active and active_only:
                if self.show_inactive:
                    self.trace(target, nid, f"[Off] {lbl}", history, disabled=True)
                continue
            edge_lbl = lbl if is_active else f"[Off] {lbl}"
            self.trace(target, nid, edge_lbl, history, disabled=not is_active)

        if inactive_rule_lines:
            for node in self.nodes:
                if node["data"]["id"] == nid:
                    existing = node["data"].get("tooltip", "")
                    extra = "Inactive Rules:\n" + "\n".join(inactive_rule_lines)
                    node["data"]["tooltip"] = (
                        (existing + "\n\n" + extra).strip() if existing else extra
                    )
                    break

    def _trace_states_only(self, ext_id, nid, history, live_states,
                           skip_bh=False, active_only=False):
        """Draw the well-known states from the shortcut endpoints when the detailed
        answering-rule list returned nothing. Mirrors the main loop's drawing rules."""
        for r_type, info in live_states.items():
            if skip_bh and r_type == "BusinessHours":
                continue
            target = info.get("target")
            if not target:
                continue
            is_active = info.get("enabled", True)
            lbl = "After Hours" if r_type == "AfterHours" else "Business Hours"
            if not is_active and active_only:
                if self.show_inactive:
                    self.trace(target, nid, f"[Off] {lbl}", history, disabled=True)
                continue
            edge_lbl = lbl if is_active else f"[Off] {lbl}"
            self.trace(target, nid, edge_lbl, history, disabled=not is_active)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def _trace_entry(self, start_ext_id):
        """Trace a single entry point onto the shared graph and return the id
        of the entry node created (for highlighting). Shared downstream nodes
        are automatically merged via node_map, so tracing several entry points
        on one tracer instance produces a single combined diagram."""
        start_ext_id = str(start_ext_id)

        # Company number — route via account-level answering rules
        if start_ext_id.startswith("company_"):
            number = start_ext_id.replace("company_", "")
            phone_nid = self._next_id()
            self.add_node(phone_nid, number, "phone",
                          sublabel="Company Number",
                          tooltip="Overview\nType: Company Number\n"
                                  "Routing: Account-level answering rules")
            self.entry_node_ids.append(phone_nid)
            self._trace_account_rules(phone_nid, called_number=number)
            return phone_nid

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
                        self.entry_node_ids.append(phone_nid)
                        self.trace(str(assigned["id"]), phone_nid, "Routes to")
                        return phone_nid

            nid = self._next_id()
            self.add_node(nid, number, "phone", sublabel="Unassigned / Not Found")
            self.entry_node_ids.append(nid)
            return nid

        # Normal extension trace
        nid = self.trace(start_ext_id)
        if nid:
            self.entry_node_ids.append(nid)
        return nid

    def generate(self, start_ext_id):
        self._trace_entry(start_ext_id)
        return (
            {"nodes": self.nodes, "edges": self.edges,
             "entry_ids": self.entry_node_ids},
            self.request_logs,
        )

    def generate_many(self, start_ext_ids):
        """Trace several entry points into one merged graph."""
        for sid in start_ext_ids:
            if sid is None or str(sid).strip() == "":
                continue
            try:
                self._trace_entry(str(sid).strip())
            except Exception as e:
                self.request_logs.append({
                    "method": "GET",
                    "endpoint": f"trace-entry:{sid}",
                    "status": "EXCEPTION",
                    "code": "0",
                    "duration": "0ms",
                    "detail": str(e),
                })
        return (
            {"nodes": self.nodes, "edges": self.edges,
             "entry_ids": self.entry_node_ids},
            self.request_logs,
        )


def generate_graph_flow(start_ext_id, show_inactive=False):
    tracer = CallFlowTracer()
    tracer.show_inactive = show_inactive
    return tracer.generate(start_ext_id)


def generate_graph_flow_multi(start_ext_ids, show_inactive=False):
    tracer = CallFlowTracer()
    tracer.show_inactive = show_inactive
    return tracer.generate_many(start_ext_ids)


def generate_graph_flow_separate(start_ext_ids, show_inactive=False):
    """Trace each entry point on its own fresh tracer so the flows stay
    independent (no shared/merged nodes). Returns a list of per-flow dicts."""
    flows = []
    for sid in start_ext_ids:
        if sid is None or str(sid).strip() == "":
            continue
        tracer = CallFlowTracer()
        tracer.show_inactive = show_inactive
        graph, logs = tracer.generate_many([str(sid).strip()])
        flows.append({"id": str(sid).strip(), "graph_data": graph, "api_log": logs})
    return flows
