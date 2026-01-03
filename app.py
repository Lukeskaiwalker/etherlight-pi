import json
import math
import os
import platform
import re
import shutil
import socket
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from flask import Flask, jsonify, request, send_from_directory
from led_driver import LedStrip, hex_to_rgb
from snmp_poller import SnmpPoller
from udp_sync import UdpSync
from display import SmallDisplay
from app_context import AppContext
from temps import TempMonitor

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')

def load_config():
    with open(CONFIG_PATH,'r') as f: return json.load(f)

def save_config(cfg):
    with open(CONFIG_PATH,'w') as f: json.dump(cfg,f,indent=2)

app = Flask(__name__, static_folder='static', static_url_path='/static')
cfg = load_config()
stop_event = threading.Event()

# Shared runtime state
runtime_lock = threading.Lock()
runtime = {
    "identify_on": False,         # global identify toggle
    "port_flash": {},             # {port: until_ts} for 3s white flash
}

# Temps
tempmon = TempMonitor(cfg); tempmon.start()

# Display (optional)
disp = SmallDisplay(cfg.get('display', {}))
if cfg.get('display',{}).get('enabled', False):
    disp.start()

# LEDs
port_count = cfg['device']['ports']['count']
leds_per_port = cfg['device'].get('leds_per_port', 2)
strip = LedStrip(port_count, leds_per_port,
    pin=cfg['led']['pin'], brightness=cfg['led']['brightness'],
    strip_type=cfg['led'].get('type','ws2812b'),
    color_order=cfg['led'].get('color_order','GRB'))
try:
    # modest 1.2s rainbow boot
    strip.rainbow_cycle(duration_sec=1.2)
except Exception:
    pass

# SNMP poller
poller = SnmpPoller(host=cfg['device']['switch_host'],
                    community=cfg['device']['snmp']['community'],
                    interval_sec=cfg['polling']['interval_sec'],
                    port_count=port_count, stop_event=stop_event)
poller.start()

# Context + sync
ctx = AppContext.init(CONFIG_PATH, poller=poller, temp_monitor=tempmon)
syncer = UdpSync(lambda: ctx.get_cfg_snapshot()); syncer.start()

def choose_link_color(speed_mbps, up, link_colors):
    if not up or not speed_mbps:
        return hex_to_rgb(link_colors.get('down','#000000'))
    buckets = ['10','100','1000','2500','10000']
    chosen = None
    for b in buckets:
        if str(speed_mbps) == b:
            chosen = b; break
    if chosen is None: chosen = '1000'
    return hex_to_rgb(link_colors.get(chosen, '#00C853'))

def choose_vlan_color(vlan, vlan_colors):
    if vlan is None: return (16,16,16)
    return hex_to_rgb(vlan_colors.get(str(vlan), '#101010'))

def _clamp(x, lo=0.0, hi=1.0):
    return hi if x>hi else lo if x<lo else x

def _pulse_factor(cfg_local, now):
    p = (cfg_local.get('pulse') or {})
    period = max(0.5, (p.get('period_ms', 2000)/1000.0))
    lo     = float(p.get('min', 0.15))
    hi     = float(p.get('max', 1.0))
    shape  = (p.get('shape','sine') or 'sine').lower()
    t = (now % period) / period  # 0..1
    if shape == 'triangle':
        f = 1.0 - abs(2.0*t - 1.0)  # 0→1→0
    else:  # sine
        f = 0.5*(1.0 + math.sin(2*math.pi*t - math.pi/2))  # 0..1 starting low
    f = lo + (hi - lo) * _clamp(f,0,1)
    return _clamp(f,0,1)

def _scale_rgb(rgb, f):
    r,g,b = rgb
    return (int(r*f), int(g*f), int(b*f))

def render_loop():
    while not stop_event.is_set():
        state = poller.get_state()
        cfg_local = ctx.get_cfg_snapshot()

        # If identify is ON -> pulse all LEDs white, ignore normal rendering
        with runtime_lock:
            ident = runtime["identify_on"]
            port_flash = dict(runtime["port_flash"])

        now = time.time()
        if ident:
            f = _pulse_factor(cfg_local, now)
            white = (255,255,255)
            rgb = _scale_rgb(white, f)
            for k in range(strip.total):
                strip._set_rgb(k, rgb)
            strip.show()
            time.sleep(0.04)
            continue

        # Normal render: VLAN (slot0) solid, Link (slot1) pulses instead of blinks
        vlan_colors = cfg_local.get('vlan_colors', {})
        link_colors = cfg_local.get('link_colors', {})
        port_count  = cfg_local['device']['ports']['count']
        leds_pp     = cfg_local['device'].get('leds_per_port', 2)

        # Pulse factor for link LEDs
        pf = _pulse_factor(cfg_local, now)

        for port in range(1, port_count+1):
            s = state.get(port, {})
            vlan = s.get('vlan'); up = s.get('up', False); speed = s.get('speed')

            # Port-level 3s flash override?
            flash_until = port_flash.get(port, 0.0)
            if flash_until and now < flash_until:
                # Solid bright white on both LEDs for this port
                strip.set_port_led(port, 0, (255,255,255))
                if leds_pp >= 2: strip.set_port_led(port, 1, (255,255,255))
                continue
            elif flash_until and now >= flash_until:
                # cleanup expired
                with runtime_lock:
                    runtime["port_flash"].pop(port, None)

            # VLAN LED (slot 0)
            vlan_rgb = choose_vlan_color(vlan, vlan_colors)
            strip.set_port_led(port, 0, vlan_rgb)

            # Link LED (slot 1) = pulse color (never fully off)
            if leds_pp >= 2:
                base_rgb = choose_link_color(speed, up, link_colors)
                strip.set_port_led(port, 1, _scale_rgb(base_rgb, pf))

        strip.show()
        time.sleep(0.04)

renderer = threading.Thread(target=render_loop, daemon=True); renderer.start()

# -------------------- Routes --------------------

@app.route('/')
def index(): return send_from_directory('static', 'index.html')

@app.route('/setup')
def setup_page(): return send_from_directory('static', 'setup.html')

@app.get('/api/version')
def api_version():
    return {'version': _read_version()}

@app.post('/api/reload')
def api_reload():
    import subprocess
    threading.Thread(target=lambda: subprocess.call(['sudo','systemctl','restart','etherlight.service'] ),
                     daemon=True).start()
    return {'ok': True}

@app.route('/api/config', methods=['GET','POST'])
def api_config():
    if request.method == 'GET': return jsonify(ctx.get_cfg_snapshot())
    data = request.get_json(force=True)
    save_config(data); ctx.load_cfg()
    return jsonify({'ok': True})

@app.get('/api/state')
def api_state(): return jsonify(poller.get_state())

@app.get('/api/temps')
def api_temps(): return jsonify(tempmon.get_snapshot())

@app.post('/api/test/set')
def api_test_set():
    data = request.get_json(force=True)
    port = int(data.get('port',1)); slot=int(data.get('slot',0)); color=data.get('color','#FFFFFF')
    strip.set_port_led(port, slot, hex_to_rgb(color)); strip.show()
    return jsonify({'ok': True})

# Identify toggle
@app.route('/api/identify', methods=['GET','POST'])
def api_identify():
    if request.method == 'GET':
        with runtime_lock: return jsonify({'on': runtime['identify_on']})
    # POST: toggle or set explicit state
    try: body = request.get_json(force=True)
    except Exception: body = {}
    with runtime_lock:
        if 'on' in (body or {}):
            runtime['identify_on'] = bool(body['on'])
        else:
            runtime['identify_on'] = not runtime['identify_on']
    return jsonify({'ok': True, 'on': runtime['identify_on']})

# Per-port 3s white flash
@app.post('/api/port_blink')
def api_port_blink():
    data = request.get_json(force=True)
    port = int(data.get('port', 1))
    seconds = float(data.get('seconds', 3.0))
    until = time.time() + max(0.2, seconds)
    with runtime_lock:
        runtime['port_flash'][port] = until
    return jsonify({'ok': True, 'port': port, 'until': until})

# Auto-detect switch
# --- Detect switch -----------------------------------------------------------
@app.post('/api/detect_switch')
def api_detect_switch():
    try:
        import asyncio
        loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        res = loop.run_until_complete(poller.detect_switch())
        loop.close()

        model = guessed = sysname = None
        if isinstance(res, (list, tuple)):
            if len(res) == 3:
                model, guessed, sysname = res
            elif len(res) == 2:
                model, guessed = res
            elif len(res) == 1:
                (model,) = res
        else:
            model = res

        local = ctx.get_cfg_snapshot()
        dev = local.setdefault('device', {})
        dev.setdefault('ports', {})
        if guessed:
            dev['ports']['count'] = int(guessed)
        if model is not None:
            dev['model_hint'] = str(model)
        if sysname:
            dev['switch_name'] = str(sysname)

        save_config(local); ctx.load_cfg()
        return jsonify({'ok': True,
                        'model': dev.get('model_hint',''),
                        'ports': dev.get('ports',{}).get('count'),
                        'switch_name': dev.get('switch_name','')})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# Optional: network config endpoint (DHCP/static) — if your setup page uses it
@app.post('/api/network')
def api_network():
    import subprocess, re as _re, shutil
    data = request.get_json(force=True) if request.data else {}
    mode = (data.get('mode') or 'dhcp').lower()
    iface = 'eth0'
    lines = []
    lines.append('# EtherPi managed block')
    lines.append(f'interface {iface}')
    if mode == 'dhcp':
        # Clear static config
        block = '\n'.join(lines) + '\n'
    else:
        ip_cidr = (data.get('ip_cidr') or '').strip()
        gw = (data.get('gateway') or '').strip()
        dns = [d.strip() for d in (data.get('dns','').split(',') if data.get('dns') else []) if d.strip()]
        if not ip_cidr or not gw:
            return ('missing ip/gateway', 400)
        lines.append(f'static ip_address={ip_cidr}')
        lines.append(f'static routers={gw}')
        if dns: lines.append('static domain_name_servers=' + ' '.join(dns))
        block = '\n'.join(lines) + '\n'
    target = '/etc/dhcpcd.conf'
    try: shutil.copy2(target, target + '.bak')
    except Exception: pass
    try:
        with open(target,'r') as f: cur = f.read()
    except Exception:
        cur = ''
    cur = _re.sub(r'# EtherPi managed block[\s\S]*?(?=\n#|\Z)','',cur, flags=_re.M)
    new = cur.rstrip() + '\n\n' + block
    with open(target,'w') as f: f.write(new)
    try: subprocess.run(['sudo','systemctl','restart','dhcpcd'], check=False)
    except Exception: pass
    return {'ok': True, 'mode': mode}


# --- System info + update helpers ---
BASE_DIR = os.path.dirname(__file__)
VERSION_PATH = os.path.join(BASE_DIR, 'VERSION')
GITHUB_REPO = os.environ.get('ETHERLIGHT_GITHUB_REPO', 'Lukeskaiwalker/etherlight-pi')

UPDATE_LOCK = threading.Lock()
UPDATE_STATE = {
    "status": "idle",
    "message": "",
    "current": None,
    "latest": None,
    "update_available": None,
    "last_checked": None,
    "last_action": None,
    "release_url": None,
    "asset_url": None,
    "asset_name": None,
}

def _read_version():
    try:
        with open(VERSION_PATH, 'r') as f:
            v = f.read().strip()
        return v or 'dev'
    except Exception:
        return 'dev'

def _parse_version(ver):
    if not ver:
        return None
    s = ver.strip()
    if s[:1].lower() == 'v':
        s = s[1:]
    parts = s.split('.')
    if len(parts) < 3:
        return None
    try:
        return tuple(int(p) for p in parts[:3])
    except ValueError:
        return None

def _version_is_newer(latest, current):
    l = _parse_version(latest)
    c = _parse_version(current)
    if not l or not c:
        return False
    return l > c

def _load_device_name():
    try:
        with open(CONFIG_PATH, 'r') as f:
            cfg = json.load(f)
        return (cfg.get('device') or {}).get('name')
    except Exception:
        return None

def _read_os_release_pretty():
    try:
        with open('/etc/os-release', 'r') as f:
            for line in f:
                if line.startswith('PRETTY_NAME='):
                    v = line.split('=', 1)[1].strip().strip('"')
                    return v
    except Exception:
        pass
    return platform.platform()

def _read_pi_model():
    try:
        p = '/proc/device-tree/model'
        if os.path.exists(p):
            return open(p, 'rb').read().decode('utf-8', 'ignore').strip('\x00').strip()
    except Exception:
        pass
    return platform.machine()

def _default_iface():
    try:
        out = subprocess.check_output(
            ['ip', '-4', 'route', 'show', 'default'],
            text=True,
            stderr=subprocess.DEVNULL
        ).strip()
        m = re.search(r'\bdev\s+(\S+)', out)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None

def _ip_for_iface(iface):
    try:
        out = subprocess.check_output(
            ['ip', '-4', 'addr', 'show', 'dev', iface],
            text=True,
            stderr=subprocess.DEVNULL
        )
        m = re.search(r'\binet\s+(\d+\.\d+\.\d+\.\d+)', out)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None

def _mac_for_iface(iface):
    try:
        p = f'/sys/class/net/{iface}/address'
        if os.path.exists(p):
            return open(p, 'r').read().strip()
    except Exception:
        pass
    return None

def _fallback_ip():
    try:
        out = subprocess.check_output(['hostname', '-I'], text=True, stderr=subprocess.DEVNULL).strip()
        for token in out.split():
            if token and not token.startswith('127.'):
                return token
    except Exception:
        pass
    return None

def _primary_net_info():
    iface = _default_iface()
    ip = _ip_for_iface(iface) if iface else None
    if not ip:
        ip = _fallback_ip()
    mac = _mac_for_iface(iface) if iface else None
    if not mac:
        n = uuid.getnode()
        mac = ':'.join(f"{(n>>b)&0xff:02x}" for b in range(40, -1, -8))
    return ip or '127.0.0.1', mac, iface

def _sysinfo_payload():
    ip, mac, iface = _primary_net_info()
    return {
        "version": _read_version(),
        "pi_model": _read_pi_model(),
        "os_pretty": _read_os_release_pretty(),
        "kernel": platform.release(),
        "arch": platform.machine(),
        "hostname": socket.gethostname(),
        "ip": ip,
        "mac": mac,
        "iface": iface,
        "name": _load_device_name(),
    }

@app.route('/api/sysinfo')
def api_sysinfo():
    return jsonify(_sysinfo_payload())

@app.route('/api/system')
def api_system():
    return jsonify(_sysinfo_payload())

def _set_update_state(**kwargs):
    with UPDATE_LOCK:
        UPDATE_STATE.update(kwargs)

def _get_update_state():
    with UPDATE_LOCK:
        return dict(UPDATE_STATE)

def _run_cmd(cmd, cwd=None):
    res = subprocess.run(cmd, cwd=cwd, check=True, text=True, capture_output=True)
    return res.stdout.strip()

def _git_is_clean():
    try:
        out = _run_cmd(['git', 'status', '--porcelain'], cwd=BASE_DIR)
        return out.strip() == ''
    except Exception:
        return True

def _systemctl(*args):
    cmd = ['systemctl'] + list(args)
    if os.geteuid() != 0:
        cmd = ['sudo'] + cmd
    _run_cmd(cmd)

def _install_service_file():
    src = os.path.join(BASE_DIR, 'service', 'etherlight.service')
    if not os.path.exists(src):
        return
    dst = '/etc/systemd/system/etherlight.service'
    with open(src, 'r') as f:
        content = f.read().replace('__BASE_DIR__', BASE_DIR)
    tmp = dst + '.tmp'
    with open(tmp, 'w') as f:
        f.write(content)
    os.replace(tmp, dst)
    _systemctl('daemon-reload')

def _pip_install():
    venv_pip = os.path.join(BASE_DIR, '.venv', 'bin', 'pip')
    if os.path.exists(venv_pip):
        _run_cmd([venv_pip, 'install', '-r', 'requirements.txt'], cwd=BASE_DIR)
    else:
        _run_cmd(['python3', '-m', 'pip', 'install', '-r', 'requirements.txt'], cwd=BASE_DIR)

def _restart_service():
    _systemctl('restart', 'etherlight.service')

def _fetch_latest_tag():
    url = f'https://api.github.com/repos/{GITHUB_REPO}/tags?per_page=1'
    req = urllib.request.Request(url, headers={'User-Agent': 'Etherlight-Pi'})
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.load(resp)
    if isinstance(data, list) and data:
        tag = (data[0].get('name') or '').strip()
        return tag[1:] if tag.lower().startswith('v') else tag
    return None

def _fetch_latest_release():
    url = f'https://api.github.com/repos/{GITHUB_REPO}/releases/latest'
    req = urllib.request.Request(url, headers={'User-Agent': 'Etherlight-Pi'})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return _fetch_latest_tag(), None, None, None
        raise
    tag = (data.get('tag_name') or data.get('name') or '').strip()
    latest = tag[1:] if tag.lower().startswith('v') else tag
    release_url = data.get('html_url')
    asset_url = None
    asset_name = None
    for asset in (data.get('assets') or []):
        name = (asset.get('name') or '').strip()
        if name.endswith(('.zip', '.tar.gz', '.tgz', '.tar')):
            asset_url = asset.get('browser_download_url')
            asset_name = name
            break
    return latest, release_url, asset_url, asset_name

@app.get('/api/update/status')
def api_update_status():
    return jsonify(_get_update_state())

@app.get('/api/update/check')
def api_update_check():
    current = _read_version()
    _set_update_state(status='checking', message='Checking for updates...', current=current)
    try:
        latest, release_url, asset_url, asset_name = _fetch_latest_release()
        update_available = _version_is_newer(latest, current) if latest else False
        msg = 'Update check complete.' if latest else 'No releases/tags found.'
        _set_update_state(
            status='ok',
            message=msg,
            current=current,
            latest=latest,
            update_available=update_available,
            last_checked=time.time(),
            release_url=release_url,
            asset_url=asset_url,
            asset_name=asset_name,
        )
        return jsonify({
            "ok": True,
            "current": current,
            "latest": latest,
            "update_available": update_available,
            "release_url": release_url,
            "asset_url": asset_url,
            "asset_name": asset_name,
        })
    except Exception as e:
        _set_update_state(
            status='error',
            message=str(e),
            current=current,
            last_checked=time.time()
        )
        return jsonify({"ok": False, "error": str(e), "current": current}), 503

def _update_in_progress():
    return _get_update_state().get('status') == 'running'

def _run_git_update(full_install=False):
    _set_update_state(status='running', message='Updating from git...', last_action='git')
    try:
        if not _git_is_clean():
            raise RuntimeError('Local changes detected. Commit/stash before updating.')
        _run_cmd(['git', 'fetch', '--tags', 'origin'], cwd=BASE_DIR)
        _run_cmd(['git', 'pull', '--ff-only'], cwd=BASE_DIR)
        if full_install:
            _run_cmd(['bash', os.path.join(BASE_DIR, 'install.sh')], cwd=BASE_DIR)
        else:
            _pip_install()
            _install_service_file()
        _restart_service()
        _set_update_state(
            status='ok',
            message='Update applied.',
            current=_read_version(),
            update_available=False
        )
    except Exception as e:
        _set_update_state(status='error', message=str(e))

def _extract_archive(path, dest_dir):
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path, 'r') as zf:
            zf.extractall(dest_dir)
    elif tarfile.is_tarfile(path):
        with tarfile.open(path, 'r:*') as tf:
            tf.extractall(dest_dir)
    else:
        raise ValueError('Unsupported archive type.')

def _copy_update_tree(src_root):
    skip_dirs = {'.git', '.venv', '__pycache__'}
    skip_files = {'config.json'}
    for root, dirs, files in os.walk(src_root):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        rel = os.path.relpath(root, src_root)
        dest_root = BASE_DIR if rel == '.' else os.path.join(BASE_DIR, rel)
        os.makedirs(dest_root, exist_ok=True)
        for fname in files:
            if fname in skip_files:
                continue
            if fname.endswith('.pyc') or fname == '.DS_Store':
                continue
            src = os.path.join(root, fname)
            dst = os.path.join(dest_root, fname)
            shutil.copy2(src, dst)

def _find_extract_root(dest_dir):
    entries = [e for e in os.listdir(dest_dir) if not e.startswith('.')]
    if len(entries) == 1:
        candidate = os.path.join(dest_dir, entries[0])
        if os.path.isdir(candidate):
            return candidate
    return dest_dir

def _run_upload_update(archive_path):
    _set_update_state(status='running', message='Applying uploaded update...', last_action='upload')
    tmp_dir = tempfile.mkdtemp(prefix='etherlight_update_')
    try:
        _extract_archive(archive_path, tmp_dir)
        root = _find_extract_root(tmp_dir)
        if not os.path.exists(os.path.join(root, 'app.py')):
            raise ValueError('Uploaded archive does not look like an Etherlight release.')
        _copy_update_tree(root)
        _pip_install()
        _install_service_file()
        _restart_service()
        _set_update_state(
            status='ok',
            message='Update applied.',
            current=_read_version(),
            update_available=False
        )
    except Exception as e:
        _set_update_state(status='error', message=str(e))
    finally:
        try:
            os.remove(archive_path)
        except Exception:
            pass
        try:
            shutil.rmtree(tmp_dir)
        except Exception:
            pass

@app.post('/api/update/pull')
def api_update_pull():
    if _update_in_progress():
        return jsonify({"ok": False, "error": "Update already in progress."}), 409
    body = request.get_json(silent=True) or {}
    full = bool(body.get('full'))
    threading.Thread(target=_run_git_update, args=(full,), daemon=True).start()
    return jsonify({"ok": True, "status": "running"})

@app.post('/api/update/upload')
def api_update_upload():
    if _update_in_progress():
        return jsonify({"ok": False, "error": "Update already in progress."}), 409
    if 'file' not in request.files:
        return jsonify({"ok": False, "error": "Missing file upload."}), 400
    upload = request.files['file']
    if not upload.filename:
        return jsonify({"ok": False, "error": "Missing filename."}), 400
    suffix = os.path.splitext(upload.filename)[1] or '.zip'
    fd, tmp_path = tempfile.mkstemp(prefix='etherlight_upload_', suffix=suffix)
    os.close(fd)
    upload.save(tmp_path)
    threading.Thread(target=_run_upload_update, args=(tmp_path,), daemon=True).start()
    return jsonify({"ok": True, "status": "running"})

@app.route('/api/poe')
def api_poe():
    # No standard MIB exposed by your USW-24-PoE; stub for UI
    return jsonify({"supported": False})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
