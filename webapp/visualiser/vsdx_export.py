# webapp/visualiser/vsdx_export.py
"""
Build a Visio .vsdx package (an OPC/zip file) from the call-flow graph data.

Lucidchart's file importer accepts Visio and — unlike its Mermaid and .drawio
importers — preserves fill colours, positions and connectors, so this is the
high-fidelity export path. The package is assembled by hand (no Visio needed):
coloured rounded rectangles for nodes, border-clipped connector lines with
mid-line labels, and optional dark "descriptor" cards carrying the detail that
otherwise only shows on hover in the tool.
"""
import io
import math
import zipfile
from xml.sax.saxutils import escape as _xml_escape

PXIN = 96.0  # px per inch — converts on-screen px layout to Visio inches

COLOURS = {
    'phone':            {'bg': '#3b82f6', 'br': '#1d4ed8', 'lt': '#69a2ff'},
    'autoreceptionist': {'bg': '#8b5cf6', 'br': '#6d28d9', 'lt': '#b191fb'},
    'ivr':              {'bg': '#06b6d4', 'br': '#0891b2', 'lt': '#4fd6ec'},
    'queue':            {'bg': '#f59e0b', 'br': '#b45309', 'lt': '#fbbf5a'},
    'user':             {'bg': '#10b981', 'br': '#047857', 'lt': '#4ad6a6'},
    'voicemail':        {'bg': '#64748b', 'br': '#475569', 'lt': '#94a3b8'},
    'external':         {'bg': '#f43f5e', 'br': '#be123c', 'lt': '#fb7185'},
    'site':             {'bg': '#334155', 'br': '#0f172a', 'lt': '#64748b'},
    'unknown':          {'bg': '#94a3b8', 'br': '#475569', 'lt': '#cbd5e1'},
}
TYPE_LABEL = {
    'phone': 'Phone Number', 'autoreceptionist': 'Auto Receptionist',
    'ivr': 'IVR Menu', 'queue': 'Call Queue', 'user': 'User / Agent',
    'voicemail': 'Voicemail', 'external': 'External Transfer',
    'site': 'Site', 'unknown': 'Unknown',
}
LEGEND_ORDER = ['phone', 'ivr', 'queue', 'autoreceptionist', 'user',
                'external', 'voicemail', 'site', 'unknown']

# Layout constants (px)
H_GAP = 60
V_GAP = 80
MARGIN = 40
DESC_W = 250
DESC_GAP = 45


def _esc(s):
    return _xml_escape(str(s if s is not None else ''))


def _node_text_lines(node, is_entry):
    lines = [l.strip() for l in str(node.get('label', '')).split('\n')
             if l.strip() and l.strip() != '─────────────']
    name = ('★ ' if is_entry else '') + (lines[0] if lines else '')
    out = [name] + lines[1:]
    if node.get('sublabel'):
        out.append(str(node['sublabel']))
    t = TYPE_LABEL.get(node.get('type'), '')
    if t:
        out.append('· ' + t)
    return out


def _descriptor_lines(node):
    """Flatten the node tooltip into printable lines (headers + bullets)."""
    raw = str(node.get('tooltip', '') or '')
    if not raw:
        return []
    out = []
    for section in raw.split('\n\n'):
        seg = [l for l in section.split('\n') if l.strip()]
        if not seg:
            continue
        out.append(seg[0].upper())
        for line in seg[1:]:
            out.append('· ' + line.strip())
        out.append('')  # blank spacer between sections
    while out and out[-1] == '':
        out.pop()
    return out


def _layered_layout(nodes, edges, entry_ids):
    ids = [n['id'] for n in nodes]
    byid = {n['id']: n for n in nodes}
    children = {i: [] for i in ids}
    indeg = {i: 0 for i in ids}
    for e in edges:
        s, t = e.get('source'), e.get('target')
        if s in byid and t in byid and s != t:
            children[s].append(t)
            indeg[t] += 1
    layer = {i: 0 for i in ids}
    roots = [i for i in ids if i in set(entry_ids) or indeg[i] == 0] or [ids[0]]
    frontier = list(roots)
    cap = len(ids) * (len(edges) + 1) + 10
    it = 0
    while frontier and it < cap:
        it += 1
        cur = frontier.pop(0)
        for c in children[cur]:
            if layer[c] < layer[cur] + 1:
                layer[c] = layer[cur] + 1
                frontier.append(c)
    return layer


def _clip_to_border(cx, cy, hw, hh, tox, toy):
    """Point where the ray from (cx,cy) toward (tox,toy) exits the box."""
    dx, dy = tox - cx, toy - cy
    if dx == 0 and dy == 0:
        return cx, cy
    sx = hw / abs(dx) if dx else float('inf')
    sy = hh / abs(dy) if dy else float('inf')
    s = min(sx, sy)
    return cx + dx * s, cy + dy * s


def _render_lines(label):
    """Split a cytoscape displayLabel into printable lines, dropping the
    divider rules the on-screen node draws."""
    out = []
    for l in str(label or '').split('\n'):
        s = l.strip()
        if not s:
            continue
        if set(s) <= set('─-—_'):  # a divider line
            continue
        out.append(s)
    return out


def _build_page_contents(graph_data, include_descriptors=False, render=None):
    """Build one Visio page's contents from a flow. Returns
    (page_contents_xml, page_width_in, page_height_in).

    If `render` is supplied (the exact node positions/sizes/labels from the
    on-screen cytoscape diagram), the page reproduces that layout 1:1 — the
    faithful way to match the visualiser. Otherwise it falls back to an
    internal layered layout from graph_data."""
    entry = set()
    desc_ids = set()

    if render and render.get('nodes'):
        # ── Faithful path: use the on-screen layout verbatim ──
        nodes, dim, pos, edges = [], {}, {}, []
        min_x = min_y = float('inf')
        raw = []
        for rn in render['nodes']:
            d = rn.get('data', rn)
            nid = str(d.get('id'))
            ntype = d.get('type', 'unknown')
            p = rn.get('position', {}) or {}
            dims = rn.get('dimensions', {}) or {}
            w = float(dims.get('w') or 200)
            h = float(dims.get('h') or 60)
            cx = float(p.get('x') or 0)
            cy = float(p.get('y') or 0)
            left, top = cx - w / 2.0, cy - h / 2.0
            min_x = min(min_x, left)
            min_y = min(min_y, top)
            if ntype == 'descriptor':
                desc_ids.add(nid)
            if d.get('isEntry'):
                entry.add(nid)
            lines = _render_lines(d.get('label'))
            raw.append((nid, ntype, left, top, w, h, lines))
        if not raw:
            raw = [('_empty', 'unknown', 0, 0, 200, 60, ['No flow data'])]
            min_x = min_y = 0
        for nid, ntype, left, top, w, h, lines in raw:
            nodes.append({'id': nid, 'type': ntype})
            dim[nid] = {'w': w, 'h': h, 'lines': lines}
            pos[nid] = {'x': left - min_x + MARGIN, 'y': top - min_y + MARGIN}
        for re_ in render.get('edges', []):
            d = re_.get('data', re_)
            e = {
                'source': str(d.get('source')), 'target': str(d.get('target')),
                'label': '' if d.get('type') == 'descriptor-edge' else (d.get('label') or ''),
                'descriptor': d.get('type') == 'descriptor-edge',
                'disabled': bool(d.get('disabled')),
            }
            mp = re_.get('midpoint')
            if isinstance(mp, dict) and mp.get('x') is not None:
                # Cytoscape's real (possibly curved) label point — normalise to
                # the same top-left origin as the nodes.
                e['lx'] = float(mp['x']) - min_x + MARGIN
                e['ly'] = float(mp['y']) - min_y + MARGIN
            edges.append(e)
        max_x = max((pos[n]['x'] + dim[n]['w'] for n in pos), default=MARGIN)
        max_y = max((pos[n]['y'] + dim[n]['h'] for n in pos), default=MARGIN)
        total_w = max_x + MARGIN
        total_h = max_y + MARGIN
    else:
        # ── Fallback path: compute a layered layout from graph_data ──
        nodes = [n['data'] for n in graph_data.get('nodes', []) if 'data' in n]
        edges = [e['data'] for e in graph_data.get('edges', []) if 'data' in e]
        entry = set(graph_data.get('entry_ids', []))

        if not nodes:
            nodes = [{'id': '_empty', 'type': 'unknown', 'label': 'No flow data',
                      'sublabel': '', 'tooltip': ''}]

        if include_descriptors:
            extra_nodes, extra_edges = [], []
            for n in list(nodes):
                desc = _descriptor_lines(n)
                if not desc:
                    continue
                did = str(n['id']) + '_desc'
                desc_ids.add(did)
                extra_nodes.append({'id': did, 'type': 'descriptor',
                                    'label': '\n'.join(desc), 'sublabel': '', 'tooltip': ''})
                extra_edges.append({'source': n['id'], 'target': did, 'descriptor': True})
            nodes = nodes + extra_nodes
            edges = edges + extra_edges

        layer = _layered_layout(nodes, edges, list(entry))
        dim = {}
        for n in nodes:
            if n['id'] in desc_ids:
                lines = [l for l in str(n['label']).split('\n')]
                longest = max([len(l) for l in lines] + [10])
                w = min(300, max(210, longest * 6 + 20))
                h = max(56, 16 + len(lines) * 13)
            else:
                lines = _node_text_lines(n, n['id'] in entry)
                longest = max([len(l) for l in lines] + [10])
                w = min(360, max(200, longest * 7 + 24))
                h = max(56, 24 + len(lines) * 16)
            dim[n['id']] = {'w': w, 'h': h, 'lines': lines}

        layers = {}
        for n in nodes:
            layers.setdefault(layer[n['id']], []).append(n)
        pos = {}
        y = MARGIN
        max_x = 0
        for k in sorted(layers):
            row = layers[k]
            row_h = max(dim[n['id']]['h'] for n in row)
            x = MARGIN
            for n in row:
                d = dim[n['id']]
                pos[n['id']] = {'x': x, 'y': y + (row_h - d['h']) / 2}
                x += d['w'] + H_GAP
            max_x = max(max_x, x)
            y += row_h + V_GAP
        total_w = max_x + MARGIN
        total_h = y + MARGIN

    # ── Legend layout (a colour key drawn below the flow) ──
    legend_types = []
    _seen_lt = set()
    for n in nodes:
        ty = n.get('type')
        if ty and ty != 'descriptor' and ty in COLOURS and ty not in _seen_lt:
            _seen_lt.add(ty)
            legend_types.append(ty)
    legend_types.sort(key=lambda t: LEGEND_ORDER.index(t) if t in LEGEND_ORDER else 99)

    LEG = None
    if legend_types:
        SW, ROWH, PADX, PADY, TITLE_H, GAP = 15, 26, 14, 12, 22, 26

        def _entry_w(t):
            return SW + 7 + int(len(TYPE_LABEL.get(t, t)) * 6.6) + GAP

        avail = max(total_w - 2 * MARGIN - 2 * PADX, 340)
        rows, rw = [[]], 0
        for t in legend_types:
            ew = _entry_w(t)
            if rw + ew > avail and rows[-1]:
                rows.append([])
                rw = 0
            rows[-1].append(t)
            rw += ew
        inner_w = max(sum(_entry_w(t) for t in r) for r in rows)
        legend_w = inner_w + 2 * PADX
        legend_h = PADY + TITLE_H + len(rows) * ROWH + PADY
        legend_x = MARGIN
        legend_y = total_h + 6  # just below the content, before the bottom margin
        total_h = legend_y + legend_h + MARGIN
        total_w = max(total_w, legend_x + legend_w + MARGIN)
        LEG = dict(rows=rows, x=legend_x, y=legend_y, w=legend_w, h=legend_h,
                   SW=SW, ROWH=ROWH, PADX=PADX, PADY=PADY, TITLE_H=TITLE_H,
                   entry_w=_entry_w)

    page_w = total_w / PXIN
    page_h = total_h / PXIN

    def to_vx(px):
        return px / PXIN

    def to_vy(px):
        return page_h - (px / PXIN)

    shapes = []
    sid = 1

    def rect_shape(_id, x, y, w, h, fill, stroke, lw, text,
                   font_color='#FFFFFF', font_size=0.10, bold=True,
                   dash=False, font_lines_left=False, grad=None, rounding=0.12):
        cxp, cyp = x + w / 2.0, y + h / 2.0
        vw, vh = w / PXIN, h / PXIN
        line_style = '<Cell N="LinePattern" V="2"/>' if dash else ''
        char = (f'<Section N="Character"><Row IX="0">'
                f'<Cell N="Color" V="{font_color}"/>'
                f'<Cell N="Size" V="{font_size}"/>'
                f'<Cell N="Style" V="{1 if bold else 0}"/></Row></Section>')
        align = ('<Section N="Paragraph"><Row IX="0"><Cell N="HorzAlign" V="0"/></Row></Section>'
                 if font_lines_left else '')
        # Optional top-lit gradient (grad = (topColor, bottomColor)). FillForegnd
        # stays a solid base colour so importers that ignore the gradient section
        # still render the node in its type colour.
        if grad:
            fill_cells = (
                f'<Cell N="FillForegnd" V="{fill}"/><Cell N="FillBkgnd" V="{grad[1]}"/>'
                f'<Cell N="FillPattern" V="1"/><Cell N="FillType" V="1"/>'
                f'<Cell N="FillGradientDir" V="0"/><Cell N="FillGradientAngle" V="1.5708"/>'
                f'<Section N="Gradient">'
                f'<Row IX="1"><Cell N="GradientStopColor" V="{grad[0]}"/>'
                f'<Cell N="GradientStopColorTrans" V="0"/><Cell N="GradientStopPosition" V="0"/></Row>'
                f'<Row IX="2"><Cell N="GradientStopColor" V="{grad[1]}"/>'
                f'<Cell N="GradientStopColorTrans" V="0"/><Cell N="GradientStopPosition" V="1"/></Row>'
                f'</Section>'
            )
        else:
            fill_cells = f'<Cell N="FillForegnd" V="{fill}"/><Cell N="FillPattern" V="1"/>'
        return (
            f'<Shape ID="{_id}" Type="Shape" LineStyle="0" FillStyle="0" TextStyle="0">'
            f'<Cell N="PinX" V="{to_vx(cxp):.4f}"/><Cell N="PinY" V="{to_vy(cyp):.4f}"/>'
            f'<Cell N="Width" V="{vw:.4f}"/><Cell N="Height" V="{vh:.4f}"/>'
            f'<Cell N="LocPinX" V="{vw / 2:.4f}" F="Width*0.5"/>'
            f'<Cell N="LocPinY" V="{vh / 2:.4f}" F="Height*0.5"/>'
            f'{fill_cells}'
            f'<Cell N="LineColor" V="{stroke}"/><Cell N="LineWeight" V="{lw}"/>'
            f'<Cell N="Rounding" V="{rounding}"/>{line_style}'
            f'{char}{align}'
            f'<Section N="Geometry" IX="0"><Cell N="NoFill" V="0"/><Cell N="NoLine" V="0"/>'
            f'<Row T="RelMoveTo" IX="1"><Cell N="X" V="0"/><Cell N="Y" V="0"/></Row>'
            f'<Row T="RelLineTo" IX="2"><Cell N="X" V="1"/><Cell N="Y" V="0"/></Row>'
            f'<Row T="RelLineTo" IX="3"><Cell N="X" V="1"/><Cell N="Y" V="1"/></Row>'
            f'<Row T="RelLineTo" IX="4"><Cell N="X" V="0"/><Cell N="Y" V="1"/></Row>'
            f'<Row T="RelLineTo" IX="5"><Cell N="X" V="0"/><Cell N="Y" V="0"/></Row></Section>'
            f'<Text>{text}</Text></Shape>'
        )

    def line_shape(_id, bx, by, ex, ey, color='#64748b', lw=0.013, dash=False,
                   text='', label_pt=None):
        # A proper Visio 1-D connector: positioned by its Begin/End points, with
        # a local (0,0)->(Width,0) geometry and its own text block — so the label
        # is part of the connector and moves with it.
        vbx, vby, vex, vey = to_vx(bx), to_vy(by), to_vx(ex), to_vy(ey)
        length = math.hypot(vex - vbx, vey - vby) or 0.001
        pinx, piny = (vbx + vex) / 2.0, (vby + vey) / 2.0
        angle = math.atan2(vey - vby, vex - vbx)
        dash_cell = '<Cell N="LinePattern" V="2"/>' if dash else ''
        txt_cells = ''
        text_el = '<Text/>'
        if str(text).strip():
            # Default: text at the line midpoint, lifted slightly off the line.
            txt_pin_x = f'<Cell N="TxtPinX" V="{length / 2:.4f}" F="Width*0.5"/>'
            txt_pin_y = '<Cell N="TxtPinY" V="0.14"/>'
            if label_pt is not None:
                # Place text at cytoscape's actual label point by mapping it into
                # the connector's local (along-line, perpendicular) frame — keeps
                # parallel/loop-back labels from piling on a node.
                lx_p, ly_p = label_pt
                dvx, dvy = lx_p - pinx, ly_p - piny
                cos_a, sin_a = math.cos(angle), math.sin(angle)
                along = dvx * cos_a + dvy * sin_a
                perp = -dvx * sin_a + dvy * cos_a
                txt_pin_x = f'<Cell N="TxtPinX" V="{length / 2 + along:.4f}"/>'
                txt_pin_y = f'<Cell N="TxtPinY" V="{perp:.4f}"/>'
            txt_cells = (
                '<Cell N="TxtWidth" V="1.6"/><Cell N="TxtHeight" V="0.24"/>'
                + txt_pin_x + txt_pin_y +
                '<Cell N="TxtLocPinX" V="0.8" F="TxtWidth*0.5"/>'
                '<Cell N="TxtLocPinY" V="0.12" F="TxtHeight*0.5"/>'
                '<Section N="Character"><Row IX="0"><Cell N="Color" V="#334155"/>'
                '<Cell N="Size" V="0.085"/></Row></Section>'
            )
            text_el = f'<Text>{_esc(text)}</Text>'
        return (
            f'<Shape ID="{_id}" Type="Shape" LineStyle="0" FillStyle="0" TextStyle="0">'
            f'<Cell N="PinX" V="{pinx:.4f}"/><Cell N="PinY" V="{piny:.4f}"/>'
            f'<Cell N="Width" V="{length:.4f}"/><Cell N="Height" V="0"/>'
            f'<Cell N="LocPinX" V="{length / 2:.4f}" F="Width*0.5"/>'
            f'<Cell N="LocPinY" V="0" F="Height*0.5"/>'
            f'<Cell N="Angle" V="{angle:.6f}"/>'
            f'<Cell N="BeginX" V="{vbx:.4f}"/><Cell N="BeginY" V="{vby:.4f}"/>'
            f'<Cell N="EndX" V="{vex:.4f}"/><Cell N="EndY" V="{vey:.4f}"/>'
            f'<Cell N="LineColor" V="{color}"/><Cell N="LineWeight" V="{lw}"/>'
            f'<Cell N="EndArrow" V="4"/>{dash_cell}{txt_cells}'
            f'<Section N="Geometry" IX="0"><Cell N="NoFill" V="1"/>'
            f'<Row T="MoveTo" IX="1"><Cell N="X" V="0"/><Cell N="Y" V="0"/></Row>'
            f'<Row T="LineTo" IX="2" F="0"><Cell N="X" V="{length:.4f}" F="Width"/><Cell N="Y" V="0"/></Row>'
            f'</Section>{text_el}</Shape>'
        )

    id_of = {}

    # Node rectangles (descriptor pseudo-nodes render as dark, left-aligned cards)
    for n in nodes:
        d = dim[n['id']]
        id_of[n['id']] = sid
        if n['id'] in desc_ids:
            shapes.append(rect_shape(
                sid, pos[n['id']]['x'], pos[n['id']]['y'], d['w'], d['h'],
                '#0f172a', '#334155', 0.01, _esc('\n'.join(d['lines'])),
                font_color='#E2E8F0', font_size=0.072, bold=False,
                font_lines_left=True))
        else:
            c = COLOURS.get(n.get('type'), COLOURS['unknown'])
            is_e = n['id'] in entry
            stroke = '#facc15' if is_e else c['br']
            lw = 0.03 if is_e else 0.014
            shapes.append(rect_shape(
                sid, pos[n['id']]['x'], pos[n['id']]['y'], d['w'], d['h'],
                c['bg'], stroke, lw, _esc('\n'.join(d['lines'])),
                grad=(c['lt'], c['bg'])))
        sid += 1

    # Whole-shape glue records so Lucid treats connectors as *attached* to the
    # boxes (they move/re-route with the shape). FromPart 9 = begin, 12 = end;
    # ToPart 3 = glue to the whole target shape.
    connects = []

    def glue(conn_id, src_id, tgt_id):
        connects.append(
            f'<Connect FromSheet="{conn_id}" FromCell="BeginX" FromPart="9" '
            f'ToSheet="{src_id}" ToPart="3"/>'
            f'<Connect FromSheet="{conn_id}" FromCell="EndX" FromPart="12" '
            f'ToSheet="{tgt_id}" ToPart="3"/>'
        )

    # Edges: dynamic connector lines (glued). Descriptor edges are dashed amber
    # with no label; disabled rules are dashed red.
    for e in edges:
        s, t = e.get('source'), e.get('target')
        if s not in id_of or t not in id_of:
            continue
        ds, dt = dim[s], dim[t]
        scx, scy = pos[s]['x'] + ds['w'] / 2.0, pos[s]['y'] + ds['h'] / 2.0
        tcx, tcy = pos[t]['x'] + dt['w'] / 2.0, pos[t]['y'] + dt['h'] / 2.0
        bx, by = _clip_to_border(scx, scy, ds['w'] / 2.0, ds['h'] / 2.0, tcx, tcy)
        ex, ey = _clip_to_border(tcx, tcy, dt['w'] / 2.0, dt['h'] / 2.0, scx, scy)
        if e.get('descriptor'):
            shapes.append(line_shape(sid, bx, by, ex, ey, color='#f59e0b',
                                     lw=0.01, dash=True))
        else:
            disabled = bool(e.get('disabled'))
            label_pt = None
            if e.get('lx') is not None and e.get('ly') is not None:
                label_pt = (to_vx(e['lx']), to_vy(e['ly']))
            shapes.append(line_shape(
                sid, bx, by, ex, ey, text=e.get('label', '') or '',
                color='#f43f5e' if disabled else '#64748b', dash=disabled,
                label_pt=label_pt))
        glue(sid, id_of[s], id_of[t])
        sid += 1

    # ── Legend: a bordered card with a colour swatch + label per node type ──
    if LEG:
        # container card
        shapes.append(rect_shape(
            sid, LEG['x'], LEG['y'], LEG['w'], LEG['h'], '#f8fafc', '#cbd5e1',
            0.008, '', rounding=0.05))
        sid += 1
        # title
        shapes.append(rect_shape(
            sid, LEG['x'] + LEG['PADX'], LEG['y'] + 5, 140, LEG['TITLE_H'],
            '#f8fafc', '#f8fafc', 0.001, _esc('LEGEND'),
            font_color='#64748b', font_size=0.085, bold=True, font_lines_left=True))
        sid += 1
        row_top = LEG['y'] + LEG['PADY'] + LEG['TITLE_H']
        for ri, row in enumerate(LEG['rows']):
            cx = LEG['x'] + LEG['PADX']
            ry = row_top + ri * LEG['ROWH']
            for t in row:
                c = COLOURS[t]
                sw_y = ry + (LEG['ROWH'] - LEG['SW']) / 2.0
                shapes.append(rect_shape(
                    sid, cx, sw_y, LEG['SW'], LEG['SW'], c['bg'], c['br'],
                    0.008, '', grad=(c['lt'], c['bg']), rounding=0.03))
                sid += 1
                lbl_w = LEG['entry_w'](t) - LEG['SW'] - 7
                shapes.append(rect_shape(
                    sid, cx + LEG['SW'] + 7, ry, lbl_w, LEG['ROWH'],
                    '#f8fafc', '#f8fafc', 0.001, _esc(TYPE_LABEL.get(t, t)),
                    font_color='#334155', font_size=0.082, bold=False,
                    font_lines_left=True))
                sid += 1
                cx += LEG['entry_w'](t)

    page_contents = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<PageContents xmlns="http://schemas.microsoft.com/office/visio/2012/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xml:space="preserve"><Shapes>' + ''.join(shapes) + '</Shapes>'
        + ('<Connects>' + ''.join(connects) + '</Connects>' if connects else '')
        + '</PageContents>'
    )

    return page_contents, page_w, page_h


def _assemble_package(pages):
    """Assemble a .vsdx OPC package from a list of pages. Each page is a dict
    {'name', 'contents', 'w', 'h'}. Multiple pages become multiple tabs that
    Lucid imports as separate pages in one document."""
    if not pages:
        pages = [{'name': 'Call Flow', 'contents':
                  '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                  '<PageContents xmlns="http://schemas.microsoft.com/office/visio/2012/main" '
                  'xml:space="preserve"><Shapes/></PageContents>', 'w': 11.0, 'h': 8.5}]

    page_els = []
    page_rels = []
    page_overrides = []
    parts = {}
    seen = set()
    for i, pg in enumerate(pages, start=1):
        name = _esc(pg.get('name') or f'Flow {i}')
        # unique NameU per tab
        base = name
        n = 1
        while name in seen:
            n += 1
            name = f'{base} ({n})'
        seen.add(name)
        w, h = pg['w'], pg['h']
        page_els.append(
            f'<Page ID="{i - 1}" NameU="{name}" Name="{name}" ViewScale="-1" '
            f'ViewCenterX="{w / 2:.4f}" ViewCenterY="{h / 2:.4f}">'
            '<PageSheet LineStyle="0" FillStyle="0" TextStyle="0">'
            f'<Cell N="PageWidth" V="{w:.4f}"/><Cell N="PageHeight" V="{h:.4f}"/>'
            '<Cell N="ShdwOffsetX" V="0.125"/><Cell N="ShdwOffsetY" V="-0.125"/>'
            '<Cell N="PageScale" V="1"/><Cell N="DrawingScale" V="1"/>'
            '<Cell N="DrawingSizeType" V="3"/><Cell N="DrawingScaleType" V="0"/>'
            f'<Cell N="InhibitSnap" V="0"/></PageSheet><Rel r:id="rId{i}"/></Page>'
        )
        page_rels.append(
            f'<Relationship Id="rId{i}" '
            'Type="http://schemas.microsoft.com/visio/2010/relationships/page" '
            f'Target="page{i}.xml"/>'
        )
        page_overrides.append(
            f'<Override PartName="/visio/pages/page{i}.xml" '
            'ContentType="application/vnd.ms-visio.page+xml"/>'
        )
        parts[f'visio/pages/page{i}.xml'] = pg['contents']

    pages_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Pages xmlns="http://schemas.microsoft.com/office/visio/2012/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xml:space="preserve">' + ''.join(page_els) + '</Pages>'
    )
    # A single default style sheet (ID 0 = "No Style") that carries line, fill
    # and text props. Every shape and the PageSheet reference LineStyle/FillStyle/
    # TextStyle="0" and DocumentSettings references Default*Style="0"; Visio treats
    # a reference to a non-existent style sheet as a corrupt document and refuses
    # to open it (Lucid silently ignores the dangling reference). Shapes still
    # override every visual cell locally, so this only satisfies the reference.
    style_sheets = (
        '<StyleSheets>'
        '<StyleSheet ID="0" NameU="No Style" Name="No Style">'
        '<Cell N="EnableLineProps" V="1"/><Cell N="EnableFillProps" V="1"/>'
        '<Cell N="EnableTextProps" V="1"/>'
        '<Cell N="LineWeight" V="0.01041666666666667"/><Cell N="LineColor" V="0"/>'
        '<Cell N="LinePattern" V="1"/><Cell N="Rounding" V="0"/>'
        '<Cell N="LineCap" V="0"/><Cell N="BeginArrow" V="0"/><Cell N="EndArrow" V="0"/>'
        '<Cell N="BeginArrowSize" V="2"/><Cell N="EndArrowSize" V="2"/>'
        '<Cell N="LineColorTrans" V="0"/>'
        '<Cell N="FillForegnd" V="1"/><Cell N="FillBkgnd" V="0"/><Cell N="FillPattern" V="1"/>'
        '<Cell N="ShdwForegnd" V="0"/><Cell N="ShdwBkgnd" V="1"/><Cell N="ShdwPattern" V="0"/>'
        '<Cell N="FillForegndTrans" V="0"/><Cell N="FillBkgndTrans" V="0"/>'
        '<Cell N="ShdwForegndTrans" V="0"/><Cell N="ShdwBkgndTrans" V="0"/>'
        '<Section N="Character"><Row IX="0">'
        '<Cell N="Font" V="Calibri"/><Cell N="Color" V="0"/><Cell N="Style" V="0"/>'
        '<Cell N="Size" V="0.1666666666666667"/><Cell N="ColorTrans" V="0"/>'
        '</Row></Section>'
        '<Section N="Paragraph"><Row IX="0"><Cell N="HorzAlign" V="1"/></Row></Section>'
        '</StyleSheet>'
        '</StyleSheets>'
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<VisioDocument xmlns="http://schemas.microsoft.com/office/visio/2012/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xml:space="preserve">'
        '<DocumentSettings TopPage="0" DefaultTextStyle="0" DefaultLineStyle="0" '
        'DefaultFillStyle="0" DefaultGuideStyle="0">'
        '<GlueSettings>9</GlueSettings><SnapSettings>65847</SnapSettings>'
        '</DocumentSettings><Colors/><FaceNames/>' + style_sheets + '</VisioDocument>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/visio/document.xml" ContentType="application/vnd.ms-visio.drawing.main+xml"/>'
        '<Override PartName="/visio/pages/pages.xml" ContentType="application/vnd.ms-visio.pages+xml"/>'
        + ''.join(page_overrides) +
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '</Types>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.microsoft.com/visio/2010/relationships/document" Target="visio/document.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/core-properties" Target="docProps/core.xml"/>'
        '</Relationships>'
    )
    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.microsoft.com/visio/2010/relationships/pages" Target="pages/pages.xml"/>'
        '</Relationships>'
    )
    pages_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + ''.join(page_rels) + '</Relationships>'
    )
    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Call Flow</dc:title>'
        '<dc:creator>Call Flow Visualiser</dc:creator></cp:coreProperties>'
    )

    parts.update({
        '[Content_Types].xml': content_types,
        '_rels/.rels': root_rels,
        'docProps/core.xml': core,
        'visio/document.xml': document,
        'visio/_rels/document.xml.rels': doc_rels,
        'visio/pages/pages.xml': pages_xml,
        'visio/pages/_rels/pages.xml.rels': pages_rels,
    })

    # Write parts in a deterministic OPC order. Visio expects [Content_Types].xml
    # (and the package relationships) to come first in the archive and will refuse
    # a package that leads with a page part; Lucid tolerates any order. Any parts
    # not named here (the page*.xml) follow in insertion order.
    order = [
        '[Content_Types].xml',
        '_rels/.rels',
        'docProps/core.xml',
        'visio/document.xml',
        'visio/_rels/document.xml.rels',
        'visio/pages/pages.xml',
        'visio/pages/_rels/pages.xml.rels',
    ]
    ordered_names = order + [n for n in parts if n not in order]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for name in ordered_names:
            z.writestr(name, parts[name])
    return buf.getvalue()


def build_vsdx(graph_data, include_descriptors=False, render=None):
    """Single-page .vsdx for one flow."""
    contents, w, h = _build_page_contents(graph_data, include_descriptors, render)
    return _assemble_package([{'name': 'Call Flow', 'contents': contents, 'w': w, 'h': h}])


def build_vsdx_multi(flows):
    """Multi-page .vsdx — one tab per flow. `flows` is a list of dicts with
    {'name', 'graph_data', 'render', 'include_descriptors'}."""
    pages = []
    for i, flow in enumerate(flows, start=1):
        contents, w, h = _build_page_contents(
            flow.get('graph_data') or {},
            bool(flow.get('include_descriptors', False)),
            flow.get('render'),
        )
        pages.append({'name': flow.get('name') or f'Flow {i}',
                      'contents': contents, 'w': w, 'h': h})
    return _assemble_package(pages)
