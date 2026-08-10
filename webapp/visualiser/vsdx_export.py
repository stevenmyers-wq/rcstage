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
import zipfile
from xml.sax.saxutils import escape as _xml_escape

PXIN = 96.0  # px per inch — converts on-screen px layout to Visio inches

COLOURS = {
    'phone':            {'bg': '#3b82f6', 'br': '#1d4ed8'},
    'autoreceptionist': {'bg': '#8b5cf6', 'br': '#6d28d9'},
    'ivr':              {'bg': '#06b6d4', 'br': '#0891b2'},
    'queue':            {'bg': '#f59e0b', 'br': '#b45309'},
    'user':             {'bg': '#10b981', 'br': '#047857'},
    'voicemail':        {'bg': '#64748b', 'br': '#475569'},
    'external':         {'bg': '#f43f5e', 'br': '#be123c'},
    'site':             {'bg': '#334155', 'br': '#0f172a'},
    'unknown':          {'bg': '#94a3b8', 'br': '#475569'},
}
TYPE_LABEL = {
    'phone': 'Phone Number', 'autoreceptionist': 'Auto Receptionist',
    'ivr': 'IVR Menu', 'queue': 'Call Queue', 'user': 'User / Agent',
    'voicemail': 'Voicemail', 'external': 'External Transfer',
    'site': 'Site', 'unknown': 'Unknown',
}

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


def build_vsdx(graph_data):
    """Return the bytes of a .vsdx package for the given graph data dict
    ({'nodes': [...], 'edges': [...], 'entry_ids': [...]})."""
    nodes = [n['data'] for n in graph_data.get('nodes', []) if 'data' in n]
    edges = [e['data'] for e in graph_data.get('edges', []) if 'data' in e]
    entry = set(graph_data.get('entry_ids', []))
    byid = {n['id']: n for n in nodes}

    if not nodes:
        nodes = [{'id': '_empty', 'type': 'unknown', 'label': 'No flow data',
                  'sublabel': '', 'tooltip': ''}]

    layer = _layered_layout(nodes, edges, list(entry))

    # Node sizes + descriptor content
    dim = {}
    for n in nodes:
        lines = _node_text_lines(n, n['id'] in entry)
        longest = max([len(l) for l in lines] + [10])
        w = min(360, max(200, longest * 7 + 24))
        h = max(56, 24 + len(lines) * 16)
        desc = _descriptor_lines(n)
        dim[n['id']] = {'w': w, 'h': h, 'lines': lines, 'desc': desc}

    # Group by layer, lay out left→right reserving room for descriptor cards
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
            reserve = d['w'] + (DESC_W + DESC_GAP if d['desc'] else 0)
            x += reserve + H_GAP
        max_x = max(max_x, x)
        y += row_h + V_GAP

    total_w = max_x + MARGIN + DESC_W
    total_h = y + MARGIN
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
                   dash=False, font_lines_left=False):
        cxp, cyp = x + w / 2.0, y + h / 2.0
        vw, vh = w / PXIN, h / PXIN
        line_style = '<Cell N="LinePattern" V="2"/>' if dash else ''
        char = (f'<Section N="Character"><Row IX="0">'
                f'<Cell N="Color" V="{font_color}"/>'
                f'<Cell N="Size" V="{font_size}"/>'
                f'<Cell N="Style" V="{1 if bold else 0}"/></Row></Section>')
        align = ('<Section N="Paragraph"><Row IX="0"><Cell N="HorzAlign" V="0"/></Row></Section>'
                 if font_lines_left else '')
        return (
            f'<Shape ID="{_id}" Type="Shape" LineStyle="0" FillStyle="0" TextStyle="0">'
            f'<Cell N="PinX" V="{to_vx(cxp):.4f}"/><Cell N="PinY" V="{to_vy(cyp):.4f}"/>'
            f'<Cell N="Width" V="{vw:.4f}"/><Cell N="Height" V="{vh:.4f}"/>'
            f'<Cell N="LocPinX" V="{vw / 2:.4f}" F="Width*0.5"/>'
            f'<Cell N="LocPinY" V="{vh / 2:.4f}" F="Height*0.5"/>'
            f'<Cell N="FillForegnd" V="{fill}"/><Cell N="FillPattern" V="1"/>'
            f'<Cell N="LineColor" V="{stroke}"/><Cell N="LineWeight" V="{lw}"/>'
            f'<Cell N="Rounding" V="0.08"/>{line_style}'
            f'{char}{align}'
            f'<Section N="Geometry" IX="0"><Cell N="NoFill" V="0"/><Cell N="NoLine" V="0"/>'
            f'<Row T="RelMoveTo" IX="1"><Cell N="X" V="0"/><Cell N="Y" V="0"/></Row>'
            f'<Row T="RelLineTo" IX="2"><Cell N="X" V="1"/><Cell N="Y" V="0"/></Row>'
            f'<Row T="RelLineTo" IX="3"><Cell N="X" V="1"/><Cell N="Y" V="1"/></Row>'
            f'<Row T="RelLineTo" IX="4"><Cell N="X" V="0"/><Cell N="Y" V="1"/></Row>'
            f'<Row T="RelLineTo" IX="5"><Cell N="X" V="0"/><Cell N="Y" V="0"/></Row></Section>'
            f'<Text>{text}</Text></Shape>'
        )

    def line_shape(_id, bx, by, ex, ey, color='#64748b', lw=0.013, dash=False):
        vbx, vby, vex, vey = to_vx(bx), to_vy(by), to_vx(ex), to_vy(ey)
        dash_cell = '<Cell N="LinePattern" V="2"/>' if dash else ''
        return (
            f'<Shape ID="{_id}" Type="Shape" LineStyle="0" FillStyle="0" TextStyle="0">'
            f'<Cell N="BeginX" V="{vbx:.4f}"/><Cell N="BeginY" V="{vby:.4f}"/>'
            f'<Cell N="EndX" V="{vex:.4f}"/><Cell N="EndY" V="{vey:.4f}"/>'
            f'<Cell N="LineColor" V="{color}"/><Cell N="LineWeight" V="{lw}"/>'
            f'<Cell N="EndArrow" V="4"/>{dash_cell}'
            f'<Section N="Geometry" IX="0"><Cell N="NoFill" V="1"/>'
            f'<Row T="MoveTo" IX="1"><Cell N="X" V="{vbx:.4f}"/><Cell N="Y" V="{vby:.4f}"/></Row>'
            f'<Row T="LineTo" IX="2"><Cell N="X" V="{vex:.4f}"/><Cell N="Y" V="{vey:.4f}"/></Row>'
            f'</Section></Shape>'
        )

    def label_shape(_id, mx, my, text):
        # Small text-only shape centred on the line midpoint.
        vw, vh = 1.6, 0.22
        return (
            f'<Shape ID="{_id}" Type="Shape" LineStyle="0" FillStyle="0" TextStyle="0">'
            f'<Cell N="PinX" V="{to_vx(mx):.4f}"/><Cell N="PinY" V="{to_vy(my):.4f}"/>'
            f'<Cell N="Width" V="{vw}"/><Cell N="Height" V="{vh}"/>'
            f'<Cell N="LocPinX" V="{vw / 2}" F="Width*0.5"/>'
            f'<Cell N="LocPinY" V="{vh / 2}" F="Height*0.5"/>'
            f'<Cell N="FillForegnd" V="#FFFFFF"/><Cell N="FillPattern" V="1"/>'
            f'<Cell N="LineColor" V="#FFFFFF"/><Cell N="LineWeight" V="0"/>'
            f'<Cell N="LinePattern" V="0"/>'
            f'<Section N="Character"><Row IX="0"><Cell N="Color" V="#334155"/>'
            f'<Cell N="Size" V="0.085"/></Row></Section>'
            f'<Text>{_esc(text)}</Text></Shape>'
        )

    id_of = {}

    # Node rectangles
    for n in nodes:
        d = dim[n['id']]
        c = COLOURS.get(n.get('type'), COLOURS['unknown'])
        is_e = n['id'] in entry
        stroke = '#facc15' if is_e else c['br']
        lw = 0.03 if is_e else 0.014
        id_of[n['id']] = sid
        shapes.append(rect_shape(
            sid, pos[n['id']]['x'], pos[n['id']]['y'], d['w'], d['h'],
            c['bg'], stroke, lw, _esc('\n'.join(d['lines']))))
        sid += 1

    # Descriptor cards (dark) + dashed connectors
    for n in nodes:
        d = dim[n['id']]
        if not d['desc']:
            continue
        nx = pos[n['id']]['x']
        ny = pos[n['id']]['y']
        dh = max(56, 20 + len(d['desc']) * 14)
        dx = nx + d['w'] + DESC_GAP
        dy = ny + (d['h'] - dh) / 2.0
        # dashed connector: node right edge → descriptor left edge
        sy = ny + d['h'] / 2.0
        dsy = dy + dh / 2.0
        shapes.append(line_shape(sid, nx + d['w'], sy, dx, dsy,
                                 color='#f59e0b', lw=0.01, dash=True))
        sid += 1
        shapes.append(rect_shape(
            sid, dx, dy, DESC_W, dh, '#0f172a', '#334155', 0.01,
            _esc('\n'.join(d['desc'])), font_color='#E2E8F0',
            font_size=0.075, bold=False, font_lines_left=True))
        sid += 1

    # Edges: border-clipped lines + mid-line labels
    for e in edges:
        s, t = e.get('source'), e.get('target')
        if s not in id_of or t not in id_of:
            continue
        ds, dt = dim[s], dim[t]
        scx, scy = pos[s]['x'] + ds['w'] / 2.0, pos[s]['y'] + ds['h'] / 2.0
        tcx, tcy = pos[t]['x'] + dt['w'] / 2.0, pos[t]['y'] + dt['h'] / 2.0
        bx, by = _clip_to_border(scx, scy, ds['w'] / 2.0, ds['h'] / 2.0, tcx, tcy)
        ex, ey = _clip_to_border(tcx, tcy, dt['w'] / 2.0, dt['h'] / 2.0, scx, scy)
        shapes.append(line_shape(sid, bx, by, ex, ey))
        sid += 1
        lbl = e.get('label', '') or ''
        if str(lbl).strip():
            shapes.append(label_shape(sid, (bx + ex) / 2.0, (by + ey) / 2.0, lbl))
            sid += 1

    page_contents = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<PageContents xmlns="http://schemas.microsoft.com/office/visio/2012/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xml:space="preserve"><Shapes>' + ''.join(shapes) + '</Shapes></PageContents>'
    )

    pages = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Pages xmlns="http://schemas.microsoft.com/office/visio/2012/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xml:space="preserve">'
        f'<Page ID="0" NameU="Call Flow" Name="Call Flow" ViewScale="-1" '
        f'ViewCenterX="{page_w / 2:.4f}" ViewCenterY="{page_h / 2:.4f}">'
        '<PageSheet LineStyle="0" FillStyle="0" TextStyle="0">'
        f'<Cell N="PageWidth" V="{page_w:.4f}"/><Cell N="PageHeight" V="{page_h:.4f}"/>'
        '<Cell N="ShdwOffsetX" V="0.125"/><Cell N="ShdwOffsetY" V="-0.125"/>'
        '<Cell N="PageScale" V="1"/><Cell N="DrawingScale" V="1"/>'
        '<Cell N="DrawingSizeType" V="3"/><Cell N="DrawingScaleType" V="0"/>'
        '<Cell N="InhibitSnap" V="0"/></PageSheet><Rel r:id="rId1"/></Page></Pages>'
    )

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<VisioDocument xmlns="http://schemas.microsoft.com/office/visio/2012/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xml:space="preserve">'
        '<DocumentSettings TopPage="0" DefaultTextStyle="0" DefaultLineStyle="0" '
        'DefaultFillStyle="0" DefaultGuideStyle="0">'
        '<GlueSettings>9</GlueSettings><SnapSettings>65847</SnapSettings>'
        '</DocumentSettings><Colors/><FaceNames/><StyleSheets/></VisioDocument>'
    )

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/visio/document.xml" ContentType="application/vnd.ms-visio.drawing.main+xml"/>'
        '<Override PartName="/visio/pages/pages.xml" ContentType="application/vnd.ms-visio.pages+xml"/>'
        '<Override PartName="/visio/pages/page1.xml" ContentType="application/vnd.ms-visio.page+xml"/>'
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
        '<Relationship Id="rId1" Type="http://schemas.microsoft.com/visio/2010/relationships/page" Target="page1.xml"/>'
        '</Relationships>'
    )
    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Call Flow</dc:title>'
        '<dc:creator>Call Flow Visualiser</dc:creator></cp:coreProperties>'
    )

    parts = {
        '[Content_Types].xml': content_types,
        '_rels/.rels': root_rels,
        'docProps/core.xml': core,
        'visio/document.xml': document,
        'visio/_rels/document.xml.rels': doc_rels,
        'visio/pages/pages.xml': pages,
        'visio/pages/_rels/pages.xml.rels': pages_rels,
        'visio/pages/page1.xml': page_contents,
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, data in parts.items():
            z.writestr(name, data)
    return buf.getvalue()
