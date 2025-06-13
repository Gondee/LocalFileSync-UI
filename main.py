"""
Unison Sync Orchestrator – v 3.13
• Same feature-set as 3.12, but includes the missing pick_directory helper.
"""

import os, subprocess, threading, time
from dearpygui import dearpygui as dpg

# ─────────────────────────── CONSTANTS ───────────────────────────
UNISON_DIR   = os.path.expanduser("~/.unison")
ORCH_DIR     = os.path.expanduser("~/unison_orchestrator")
DEFAULT_SYNC = os.path.join(ORCH_DIR, "sync_all_profiles.sh")
DEFAULT_CRON = "0 0 * * *"
MONITOR_SECS = 5
TAIL_LINES   = 40
EDIT_W, EDIT_H = 650, 470

os.makedirs(UNISON_DIR, exist_ok=True)
os.makedirs(ORCH_DIR,    exist_ok=True)

# ─────────────────────────── GENERIC HELPERS ───────────────────────────
def resolve_script_path(token: str) -> str:
    token = os.path.expandvars(os.path.expanduser(token))
    return token if os.path.isabs(token) else os.path.join(ORCH_DIR, token)

def cron_scripts_from_crontab():
    try:
        text = subprocess.check_output("crontab -l", shell=True, text=True)
    except subprocess.CalledProcessError:
        text = ""
    scripts = []
    for ln in text.splitlines():
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        parts = ln.split()
        for tok in parts[5:]:
            if tok.strip('"\'' ).endswith(".sh"):
                scripts.append(resolve_script_path(tok.strip('"\' ')))
                break
    return sorted(set(scripts))

def load_profiles():
    return sorted(f[:-4] for f in os.listdir(UNISON_DIR) if f.endswith(".prf"))

def last_sync(profile):
    path = os.path.join(UNISON_DIR, f"{profile}.log")
    if not os.path.exists(path):
        return "No Sync History"
    with open(path) as f:
        for line in reversed(f.readlines()):
            if line.startswith("Synchronization complete at"):
                return line.replace("Synchronization complete at","").strip()
    return "No Sync History"

def get_crontab_text():
    try:
        return subprocess.check_output("crontab -l", shell=True, text=True)
    except subprocess.CalledProcessError:
        return "(no crontab)"

def running_unison_processes():
    try:
        lines = subprocess.check_output("pgrep -fl unison", shell=True,
                                        text=True).splitlines()
    except subprocess.CalledProcessError:
        lines = []
    return "\n".join(lines) or "(none)"

def tail_log(profile):
    path = os.path.join(UNISON_DIR, f"{profile}.log")
    if not os.path.exists(path):
        return "(no log)"
    with open(path) as f:
        return "".join(f.readlines()[-TAIL_LINES:]) or "(empty)"

# ─────────────────────────── FILE-DIALOG HELPERS ───────────────────────────
def _set_value_from_dialog(sender, app_data, field_tag):
    """Write chosen folder into its input field."""
    dpg.set_value(field_tag, os.path.expanduser(app_data["current_path"]))

# map field -> file_dialog tag
FIELD_TO_DIALOG = {
    "new_src":  "dlg_new_src",
    "new_tgt":  "dlg_new_tgt",
    "edit_src": "dlg_edit_src",
    "edit_tgt": "dlg_edit_tgt",
}
def pick_directory(field_tag):
    dpg.show_item(FIELD_TO_DIALOG[field_tag])

# ─────────────────────────── PROFILE ACTIONS ───────────────────────────
def run_sync(sender, app_data, profile):
    subprocess.run(["unison", profile, "-batch"])
    dpg.set_value(f"sync_{profile}", f"Last Sync: {last_sync(profile)}")

def open_profile_editor(sender, app_data, profile):
    prf = os.path.join(UNISON_DIR, f"{profile}.prf")
    src = tgt = ""
    with open(prf) as f:
        for l in f:
            if l.startswith("root = "):
                if not src: src = l[7:].strip()
                else:       tgt = l[7:].strip()
    dpg.set_value("edit_name", profile)
    dpg.set_value("edit_src",  src)
    dpg.set_value("edit_tgt",  tgt)
    dpg.set_value("edit_msg",  "")

    w, h = dpg.get_viewport_client_width(), dpg.get_viewport_client_height()
    dpg.configure_item("edit_win",
                       pos=(max(0, w//2 - EDIT_W//2),
                            max(0, h//2 - EDIT_H//2)),
                       show=True)

def delete_profile(sender, app_data, profile):
    for ext in (".prf", ".log"):
        p = os.path.join(UNISON_DIR, f"{profile}{ext}")
        if os.path.exists(p):
            os.remove(p)
    dpg.configure_item("new_msg",
                       default_value=f"Deleted {profile}.", color=[200,40,40])
    refresh_profile_panel()

# ─────────────────────────── PROFILE SAVE OPS ───────────────────────────
def save_new_profile():
    name = dpg.get_value("new_name").strip()
    src  = dpg.get_value("new_src").strip()
    tgt  = dpg.get_value("new_tgt").strip()
    if not (name and src and tgt):
        dpg.configure_item("new_msg", default_value="All fields required.",
                           color=[255,0,0]); return
    with open(os.path.join(UNISON_DIR,f"{name}.prf"),"w") as f:
        f.write(f"root = {src}\nroot = {tgt}\n\nauto = true\nbatch = true\n"
                f"prefer = newer\nlog = true\nlogfile = {UNISON_DIR}/{name}.log\n")
    dpg.configure_item("new_msg", default_value="Profile saved.",
                       color=[0,255,0])
    refresh_profile_panel(); refresh_monitor_tab()

def save_profile_edit():
    name = dpg.get_value("edit_name")
    src  = dpg.get_value("edit_src").strip()
    tgt  = dpg.get_value("edit_tgt").strip()
    if not (src and tgt):
        dpg.configure_item("edit_msg", default_value="All fields required.",
                           color=[255,0,0]); return
    with open(os.path.join(UNISON_DIR,f"{name}.prf"),"w") as f:
        f.write(f"root = {src}\nroot = {tgt}\n\nauto = true\nbatch = true\n"
                f"prefer = newer\nlog = true\nlogfile = {UNISON_DIR}/{name}.log\n")
    dpg.configure_item("edit_msg", default_value="Profile updated.",
                       color=[0,255,0])
    refresh_profile_panel()

# ─────────────────────────── PROFILE PANEL ───────────────────────────
def refresh_profile_panel():
    dpg.delete_item("profiles_panel", children_only=True)

    with dpg.theme() as del_theme:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (200,40,40))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (220,60,60))

    for profile in load_profiles():
        with dpg.group(parent="profiles_panel"):
            dpg.add_text(profile, color=(0,255,255))
            dpg.add_text(f"Last Sync: {last_sync(profile)}",
                         tag=f"sync_{profile}")
            with dpg.group(horizontal=True):
                dpg.add_button(label="Run Sync", width=80,
                               callback=run_sync, user_data=profile)
                dpg.add_button(label="Edit", width=60,
                               callback=open_profile_editor,
                               user_data=profile)
                del_btn = dpg.add_button(label="Delete", width=70,
                                         callback=delete_profile,
                                         user_data=profile)
                dpg.bind_item_theme(del_btn, del_theme)
            dpg.add_separator()

# ─────────────────────────── SCRIPT / CRON ───────────────────────────
def refresh_script_combo():
    items = cron_scripts_from_crontab()
    if DEFAULT_SYNC not in items and os.path.exists(DEFAULT_SYNC):
        items.insert(0, DEFAULT_SYNC)
    dpg.configure_item("script_combo", items=items)
    if items and dpg.get_value("script_combo") not in items:
        dpg.set_value("script_combo", items[0])
    load_selected_script()

def load_selected_script(sender=None, app_data=None, user_data=None):
    raw = dpg.get_value("script_combo")
    if not raw:
        dpg.set_value("script_txt",""); return
    try:
        with open(resolve_script_path(raw)) as f:
            dpg.set_value("script_txt", f.read())
    except FileNotFoundError:
        dpg.set_value("script_txt", "# File not found.\n")

def save_script_changes():
    raw = dpg.get_value("script_combo")
    if not raw:
        dpg.configure_item("orc_msg", default_value="No script selected.",
                           color=[255,0,0]); return
    try:
        with open(resolve_script_path(raw),"w") as f:
            f.write(dpg.get_value("script_txt"))
        os.chmod(resolve_script_path(raw),0o755)
        dpg.configure_item("orc_msg", default_value="Script saved.",
                           color=[0,255,0])
    except Exception as e:
        dpg.configure_item("orc_msg", default_value=str(e), color=[255,0,0])

def generate_parallel_script():
    with open(DEFAULT_SYNC,"w") as f:
        f.write("#!/bin/bash\n\n")
        for p in load_profiles():
            f.write(f'unison \"{p}\" -batch &\n')
        f.write("wait\n")
    os.chmod(DEFAULT_SYNC,0o755)
    dpg.configure_item("orc_msg",
                       default_value="sync_all_profiles.sh regenerated.",
                       color=[0,255,0])
    refresh_script_combo()

def cron_entry(raw): return f"{dpg.get_value('cron_sched') or DEFAULT_CRON} {resolve_script_path(raw)} >> ~/unison_cron.log 2>&1"

def install_or_update_cron():
    raw=dpg.get_value("script_combo")
    if not raw:
        dpg.configure_item("orc_msg", default_value="Select a script first.",
                           color=[255,0,0]); return
    path=resolve_script_path(raw)
    new_line=cron_entry(raw)
    cur=subprocess.run("crontab -l",shell=True,text=True,capture_output=True)
    lines=cur.stdout.splitlines() if cur.returncode==0 else []
    lines=[ln for ln in lines if path not in ln]
    lines.append(new_line)
    subprocess.run("crontab -",input="\n".join(lines)+"\n",
                   shell=True,text=True)
    dpg.configure_item("orc_msg", default_value="Cron installed/updated.",
                       color=[0,255,0]); refresh_monitor_tab()

def remove_cron_for_script():
    raw=dpg.get_value("script_combo")
    if not raw: return
    path=resolve_script_path(raw)
    cur=subprocess.run("crontab -l",shell=True,text=True,capture_output=True)
    if cur.returncode!=0: return
    lines=[ln for ln in cur.stdout.splitlines() if path not in ln]
    subprocess.run("crontab -",input="\n".join(lines)+"\n",
                   shell=True,text=True)
    dpg.configure_item("orc_msg", default_value="Cron entry removed.",
                       color=[255,255,0]); refresh_monitor_tab()

# ─────────────────────────── MONITORING LOOP ───────────────────────────
def refresh_monitor_tab():
    dpg.set_value("cron_view", get_crontab_text())
    dpg.set_value("proc_view", running_unison_processes())
    sel=dpg.get_value("log_combo")
    if sel: dpg.set_value("log_view", tail_log(sel))

def monitor_loop():
    while dpg.is_dearpygui_running():
        refresh_monitor_tab()
        time.sleep(MONITOR_SECS)

# ─────────────────────────── GUI LAYOUT ───────────────────────────
dpg.create_context()
dpg.create_viewport(title="Unison Orchestrator v3.13", width=1200, height=980)

with dpg.window(label="Unison Orchestrator v3.13", width=1180, height=960):
    dpg.add_button(label="Refresh Profiles", callback=lambda: refresh_profile_panel())
    dpg.add_spacer(height=6)
    with dpg.group(horizontal=True):
        # LEFT – profiles
        with dpg.child_window(tag="profiles_panel", width=600, height=880, border=True): pass

        # RIGHT – tabs
        with dpg.child_window(width=550, height=880, border=True):
            with dpg.tab_bar():
                # CREATE TAB
                with dpg.tab(label="Create Profile"):
                    dpg.add_input_text(label="Profile Name", tag="new_name", width=480)
                    with dpg.group(horizontal=True):
                        dpg.add_input_text(label="Source Dir", tag="new_src", width=400)
                        dpg.add_button(label="Browse", callback=lambda s,a,u: pick_directory("new_src"))
                    with dpg.group(horizontal=True):
                        dpg.add_input_text(label="Target Dir", tag="new_tgt", width=400)
                        dpg.add_button(label="Browse", callback=lambda s,a,u: pick_directory("new_tgt"))
                    dpg.add_button(label="Save Profile", callback=save_new_profile)
                    dpg.add_text("", tag="new_msg")

                # ORCHESTRATOR TAB
                with dpg.tab(label="Orchestrator"):
                    dpg.add_button(label="Generate sync_all_profiles.sh", callback=generate_parallel_script)
                    dpg.add_separator()
                    dpg.add_text("Shell scripts referenced in crontab:")
                    dpg.add_combo([], tag="script_combo", width=380,
                                  callback=load_selected_script)
                    dpg.add_button(label="Refresh List", callback=refresh_script_combo)
                    dpg.add_input_text(tag="script_txt", multiline=True,
                                       width=500, height=260)
                    dpg.add_button(label="Save Script", callback=save_script_changes)
                    dpg.add_separator()
                    dpg.add_input_text(label="Cron schedule", tag="cron_sched",
                                       width=200, default_value=DEFAULT_CRON,
                                       hint="min hr dom mon dow")
                    dpg.add_button(label="Install / Update Cron", callback=install_or_update_cron)
                    dpg.add_same_line()
                    dpg.add_button(label="Remove Cron", callback=remove_cron_for_script)
                    dpg.add_text("", tag="orc_msg")

                # MONITORING TAB
                with dpg.tab(label="Monitoring"):
                    dpg.add_text("Cron entries:")
                    dpg.add_input_text(tag="cron_view", multiline=True, readonly=True,
                                       width=500, height=120)
                    dpg.add_separator()
                    dpg.add_text("Running Unison processes:")
                    dpg.add_input_text(tag="proc_view", multiline=True, readonly=True,
                                       width=500, height=120)
                    dpg.add_separator()
                    dpg.add_text("Log viewer:")
                    dpg.add_combo(load_profiles(), tag="log_combo", width=250,
                                  callback=lambda s,a,u: refresh_monitor_tab())
                    dpg.add_input_text(tag="log_view", multiline=True, readonly=True,
                                       width=500, height=300)

# EDIT POPUP
with dpg.window(label="Edit Profile", tag="edit_win", width=EDIT_W, height=EDIT_H,
                pos=(0,0), show=False):
    dpg.add_input_text(label="Profile Name", tag="edit_name", width=500, enabled=False)
    with dpg.group(horizontal=True):
        dpg.add_input_text(label="Source Dir", tag="edit_src", width=400)
        dpg.add_button(label="Browse", callback=lambda s,a,u: pick_directory("edit_src"))
    with dpg.group(horizontal=True):
        dpg.add_input_text(label="Target Dir", tag="edit_tgt", width=400)
        dpg.add_button(label="Browse", callback=lambda s,a,u: pick_directory("edit_tgt"))
    dpg.add_button(label="Save Changes", callback=save_profile_edit)
    dpg.add_text("", tag="edit_msg")

# FILE-DIALOGS (directory selector=True)
with dpg.file_dialog(directory_selector=True, show=False, tag="dlg_new_src",
                     callback=lambda s,a,u: _set_value_from_dialog(s,a,"new_src")):
    dpg.add_file_extension(".*")
with dpg.file_dialog(directory_selector=True, show=False, tag="dlg_new_tgt",
                     callback=lambda s,a,u: _set_value_from_dialog(s,a,"new_tgt")):
    dpg.add_file_extension(".*")
with dpg.file_dialog(directory_selector=True, show=False, tag="dlg_edit_src",
                     callback=lambda s,a,u: _set_value_from_dialog(s,a,"edit_src")):
    dpg.add_file_extension(".*")
with dpg.file_dialog(directory_selector=True, show=False, tag="dlg_edit_tgt",
                     callback=lambda s,a,u: _set_value_from_dialog(s,a,"edit_tgt")):
    dpg.add_file_extension(".*")

# ───────────────────────── STARTUP ─────────────────────────
refresh_profile_panel()
refresh_script_combo()
dpg.setup_dearpygui()
dpg.show_viewport()
threading.Thread(target=monitor_loop, daemon=True).start()
dpg.start_dearpygui()
dpg.destroy_context()