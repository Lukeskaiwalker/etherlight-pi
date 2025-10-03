import json, os, threading, time
from flask import Flask, request, send_from_directory, jsonify
from led_driver import LedStrip, hex_to_rgb
from snmp_poller import SnmpPoller
from udp_sync import UdpSync
from display import SmallDisplay
from app_context import AppContext

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')
def load_config():
    with open(CONFIG_PATH,'r') as f: return json.load(f)
def save_config(cfg):
    with open(CONFIG_PATH,'w') as f: json.dump(cfg,f,indent=2)

app = Flask(__name__, static_folder='static', static_url_path='/static')
cfg = load_config()
stop_event = threading.Event()

disp = SmallDisplay(cfg.get('display', {}))
if cfg.get('display',{}).get('enabled', False): disp.start()

port_count = cfg['device']['ports']['count']
leds_per_port = cfg['device'].get('leds_per_port', 2)
strip = LedStrip(port_count, leds_per_port,
    pin=cfg['led']['pin'], brightness=cfg['led']['brightness'],
    strip_type=cfg['led'].get('type','ws2812b'),
    color_order=cfg['led'].get('color_order','GRB'))
try: strip.rainbow_cycle(duration_sec=1.2)
except Exception: pass

poller = SnmpPoller(host=cfg['device']['switch_host'],
                    community=cfg['device']['snmp']['community'],
                    interval_sec=cfg['polling']['interval_sec'],
                    port_count=port_count, stop_event=stop_event)
poller.start()

ctx = AppContext.init(CONFIG_PATH, poller=poller)
syncer = UdpSync(lambda: ctx.get_cfg_snapshot()); syncer.start()

def choose_link_color(speed_mbps, up, link_colors):
    if not up or not speed_mbps: return hex_to_rgb(link_colors.get('down','#000000')), False
    buckets = ['10','100','1000','2500','10000']; chosen=None
    for b in buckets:
        if str(speed_mbps) == b: chosen=b; break
    if chosen is None: chosen='1000'
    color_hex = link_colors.get(chosen, '#00C853'); blink = chosen in ['100','1000','2500','10000']
    return hex_to_rgb(color_hex), blink

def choose_vlan_color(vlan, vlan_colors):
    if vlan is None: return (16,16,16)
    return hex_to_rgb(vlan_colors.get(str(vlan), '#101010'))

def render_loop():
    while not stop_event.is_set():
        state = poller.get_state()
        cfg_local = ctx.get_cfg_snapshot()
        vlan_colors = cfg_local.get('vlan_colors', {})
        link_colors = cfg_local.get('link_colors', {})
        blink_cfg = cfg_local.get('blink', {'period_ms':1000,'duty':0.5})
        period = max(0.2, (blink_cfg.get('period_ms',1000)/1000.0))
        duty = min(0.95, max(0.05, float(blink_cfg.get('duty',0.5))))
        now = time.time(); phase = (now % period) / period; on_phase = phase < duty
        port_count = cfg_local['device']['ports']['count']; leds_per_port = cfg_local['device'].get('leds_per_port', 2)
        for port in range(1, port_count+1):
            s = state.get(port, {})
            vlan = s.get('vlan'); up = s.get('up', False); speed = s.get('speed')
            vlan_rgb = choose_vlan_color(vlan, vlan_colors); strip.set_port_led(port, 0, vlan_rgb)
            link_rgb, should_blink = choose_link_color(speed, up, link_colors)
            if leds_per_port >= 2:
                out_rgb = link_rgb if (not should_blink or on_phase) else (0,0,0)
                strip.set_port_led(port, 1, out_rgb)
        strip.show(); time.sleep(0.05)

renderer = threading.Thread(target=render_loop, daemon=True); renderer.start()

@app.route('/')
def index(): return send_from_directory('static', 'index.html')

@app.route('/api/config', methods=['GET','POST'])
def api_config():
    if request.method == 'GET': return jsonify(ctx.get_cfg_snapshot())
    data = request.get_json(force=True); save_config(data); ctx.load_cfg(); return jsonify({'ok': True})

@app.route('/api/state', methods=['GET'])
def api_state(): return jsonify(poller.get_state())

@app.route('/api/test/set', methods=['POST'])
def api_test_set():
    data = request.get_json(force=True); port = int(data.get('port',1)); slot=int(data.get('slot',0)); color=data.get('color','#FFFFFF')
    strip.set_port_led(port, slot, hex_to_rgb(color)); strip.show(); return jsonify({'ok': True})

@app.route('/api/identify', methods=['POST'])
def api_identify():
    mode = ctx.get_cfg_snapshot().get('identify',{}).get('mode','leds')
    if mode in ('leds','both'):
        for k in range(strip.total): strip._set_rgb(k, (255,255,255))
        strip.show(); time.sleep(ctx.get_cfg_snapshot().get('identify',{}).get('duration_ms',800)/1000.0)
        for k in range(strip.total): strip._set_rgb(k, (0,0,0))
        strip.show()
    if mode in ('screen','both'):
        try: from display import SmallDisplay; pass
        except Exception: pass
    return jsonify({'ok': True})

@app.route('/api/detect_switch', methods=['POST'])
def api_detect_switch():
    try:
        import asyncio
        loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        model, guessed = loop.run_until_complete(poller.detect_switch()); loop.close()
        local = ctx.get_cfg_snapshot()
        local['device']['ports']['count'] = int(guessed or local['device']['ports']['count'])
        local['device']['model_hint'] = str(model)
        save_config(local); ctx.load_cfg()
        return jsonify({'ok': True, 'model': model, 'ports': guessed})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
