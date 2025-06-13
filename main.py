"""
Unison Sync Orchestrator – v 3.18
• Fix: The individual "Kill" buttons in the Monitoring tab now correctly terminate the entire process
  group (`os.killpg`) instead of just a single process, ensuring that even complex `unison`
  tasks are terminated properly.
"""

import os, subprocess, threading, time, signal
from dearpygui import dearpygui as dpg

# ─────────────────────────── CONSTANTS ───────────────────────────
UNISON_DIR   = os.path.expanduser("~/.unison")
ORCH_DIR     = os.path.expanduser("~/unison_orchestrator")
DEFAULT_SYNC = os.path.join(ORCH_DIR, "sync_all_profiles.sh")
DEFAULT_CRON = "0 0 * * *"
MONITOR_SECS = 5
TAIL_LINES   = 40
EDIT_W, EDIT_H = 650, 470
LOG_W, LOG_H = 700, 500

# App state for the running script
SCRIPT_RUNNER_STATE = {"process": None, "script_path": None}

os.makedirs(UNISON_DIR, exist_ok=True)
os.makedirs(ORCH_DIR,    exist_ok=True)

# ─────────────────────────── GENERIC HELPERS ───────────────────────────
def resolve_script_path(token: str) -> str:
    token = os.path.expandvars(os.path.expanduser(token))
    return token if os.path.isabs(token) else os.path.join(ORCH_DIR, token)

def cron_scripts_from_crontab():
    try: text = subprocess.check_output("crontab -l", shell=True, text=True)
    except subprocess.CalledProcessError: text = ""
    scripts = []
    for ln in text.splitlines():
        if not ln.strip() or ln.lstrip().startswith("#"): continue
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
    if not os.path.exists(path): return "No Sync History"
    with open(path) as f:
        for line in reversed(f.readlines()):
            if line.startswith("Synchronization complete at"):
                return line.replace("Synchronization complete at","").strip()
    return "No Sync History"

def get_crontab_text():
    try: return subprocess.check_output("crontab -l", shell=True, text=True)
    except subprocess.CalledProcessError: return "(no crontab)"

def get_running_unison_processes() -> list[dict]:
    """Returns a list of dicts, each representing a running unison process."""
    processes = []
    try:
        lines = subprocess.check_output("pgrep -fl unison", shell=True, text=True).strip().splitlines()
        for line in lines:
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                processes.append({'pid': parts[0], 'cmd': parts[1]})
    except subprocess.CalledProcessError:
        pass # No processes found
    return processes

def tail_log(profile):
    path = os.path.join(UNISON_DIR, f"{profile}.log")
    if not os.path.exists(path): return "(no log)"
    with open(path) as f: return "".join(f.readlines()[-TAIL_LINES:]) or "(empty)"

# ─────────────────────────── FILE-DIALOG HELPERS ───────────────────────────
def _set_value_from_dialog(sender, app_data, field_tag):
    dpg.set_value(field_tag, os.path.expanduser(app_data["current_path"]))

FIELD_TO_DIALOG = {"new_src":"dlg_new_src", "new_tgt":"dlg_new_tgt", "edit_src":"dlg_edit_src", "edit_tgt":"dlg_edit_tgt"}
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
    dpg.set_value("edit_name", profile); dpg.set_value("edit_src",  src); dpg.set_value("edit_tgt",  tgt); dpg.set_value("edit_msg",  "")
    w, h = dpg.get_viewport_client_width(), dpg.get_viewport_client_height()
    dpg.configure_item("edit_win", pos=(max(0, w//2 - EDIT_W//2), max(0, h//2 - EDIT_H//2)), show=True)

def delete_profile(sender, app_data, profile):
    for ext in (".prf", ".log"):
        p = os.path.join(UNISON_DIR, f"{profile}{ext}")
        if os.path.exists(p): os.remove(p)
    dpg.configure_item("new_msg", default_value=f"Deleted {profile}.", color=[200,40,40])
    refresh_profile_panel()

# ─────────────────────────── PROFILE SAVE OPS ───────────────────────────
def save_new_profile():
    name, src, tgt = dpg.get_value("new_name").strip(), dpg.get_value("new_src").strip(), dpg.get_value("new_tgt").strip()
    if not (name and src and tgt):
        dpg.configure_item("new_msg", default_value="All fields required.", color=[255,0,0]); return
    with open(os.path.join(UNISON_DIR,f"{name}.prf"),"w") as f:
        f.write(f"root = {src}\nroot = {tgt}\n\nauto = true\nbatch = true\nprefer = newer\nlog = true\nlogfile = {UNISON_DIR}/{name}.log\n")
    dpg.configure_item("new_msg", default_value="Profile saved.", color=[0,255,0])
    refresh_profile_panel(); refresh_monitor_tab()

def save_profile_edit():
    name, src, tgt = dpg.get_value("edit_name"), dpg.get_value("edit_src").strip(), dpg.get_value("edit_tgt").strip()
    if not (src and tgt):
        dpg.configure_item("edit_msg", default_value="All fields required.", color=[255,0,0]); return
    with open(os.path.join(UNISON_DIR,f"{name}.prf"),"w") as f:
        f.write(f"root = {src}\nroot = {tgt}\n\nauto = true\nbatch = true\nprefer = newer\nlog = true\nlogfile = {UNISON_DIR}/{name}.log\n")
    dpg.configure_item("edit_msg", default_value="Profile updated.", color=[0,255,0])
    refresh_profile_panel()

# ─────────────────────────── PROFILE PANEL ───────────────────────────
def refresh_profile_panel():
    dpg.delete_item("profiles_panel", children_only=True)
    with dpg.theme() as del_theme:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (200,40,40)); dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (220,60,60))
    for profile in load_profiles():
        with dpg.group(parent="profiles_panel"):
            dpg.add_text(profile, color=(0,255,255))
            dpg.add_text(f"Last Sync: {last_sync(profile)}", tag=f"sync_{profile}")
            with dpg.group(horizontal=True):
                dpg.add_button(label="Run Sync", width=80, callback=run_sync, user_data=profile)
                dpg.add_button(label="Edit", width=60, callback=open_profile_editor, user_data=profile)
                del_btn = dpg.add_button(label="Delete", width=70, callback=delete_profile, user_data=profile)
                dpg.bind_item_theme(del_btn, del_theme)
            dpg.add_separator()

# ─────────────────────────── SCRIPT / CRON / LIVE RUN ───────────────────────────
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
    if not raw: dpg.set_value("script_txt",""); return
    try:
        with open(resolve_script_path(raw)) as f: dpg.set_value("script_txt", f.read())
    except FileNotFoundError: dpg.set_value("script_txt", "# File not found.\n")

def save_script_changes():
    raw = dpg.get_value("script_combo")
    if not raw: dpg.configure_item("orc_msg", default_value="No script selected.", color=[255,0,0]); return
    try:
        script_path = resolve_script_path(raw)
        with open(script_path,"w") as f: f.write(dpg.get_value("script_txt"))
        os.chmod(script_path, 0o755)
        dpg.configure_item("orc_msg", default_value="Script saved.", color=[0,255,0])
    except Exception as e: dpg.configure_item("orc_msg", default_value=str(e), color=[255,0,0])

def generate_parallel_script():
    with open(DEFAULT_SYNC,"w") as f:
        f.write("#!/bin/bash\n\n# This script runs all unison profiles in parallel.\n\n")
        for p in load_profiles(): f.write(f'unison "{p}" -batch &\n')
        f.write("\nwait\necho \"All synchronization tasks complete.\"\n")
    os.chmod(DEFAULT_SYNC,0o755)
    dpg.configure_item("orc_msg", default_value="sync_all_profiles.sh regenerated.", color=[0,255,0])
    refresh_script_combo()

def install_or_update_cron():
    raw=dpg.get_value("script_combo")
    if not raw: dpg.configure_item("orc_msg", default_value="Select a script first.", color=[255,0,0]); return
    path=resolve_script_path(raw)
    new_line = f"{dpg.get_value('cron_sched') or DEFAULT_CRON} {path} >> ~/unison_cron.log 2>&1"
    cur=subprocess.run("crontab -l",shell=True,text=True,capture_output=True)
    lines=cur.stdout.splitlines() if cur.returncode==0 else []
    lines=[ln for ln in lines if path not in ln]; lines.append(new_line)
    subprocess.run("crontab -",input="\n".join(lines)+"\n", shell=True,text=True)
    dpg.configure_item("orc_msg", default_value="Cron installed/updated.", color=[0,255,0]); refresh_monitor_tab()

def remove_cron_for_script():
    raw=dpg.get_value("script_combo");
    if not raw: return
    path=resolve_script_path(raw)
    cur=subprocess.run("crontab -l",shell=True,text=True,capture_output=True)
    if cur.returncode!=0: return
    lines=[ln for ln in cur.stdout.splitlines() if path not in ln]
    subprocess.run("crontab -",input="\n".join(lines)+"\n", shell=True,text=True)
    dpg.configure_item("orc_msg", default_value="Cron entry removed.", color=[255,255,0]); refresh_monitor_tab()

def _execute_script_and_log(script_path: str):
    try:
        process = subprocess.Popen([script_path], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, bufsize=1, universal_newlines=True, start_new_session=True)
        SCRIPT_RUNNER_STATE["process"] = process
        dpg.set_value("script_log_view", f"--- Running {os.path.basename(script_path)} ---\nPID: {process.pid}\n\n")
        for line in iter(process.stdout.readline, ''):
            current_log = dpg.get_value("script_log_view")
            dpg.set_value("script_log_view", current_log + line)
        ret_code = process.wait()
        final_message = f"\n--- SCRIPT FINISHED (Exit Code: {ret_code}) ---\n"
        if ret_code == -signal.SIGTERM:
            final_message = "\n--- SCRIPT TERMINATED BY USER ---\n"
        dpg.set_value("script_log_view", dpg.get_value("script_log_view") + final_message)
    except Exception as e:
        dpg.set_value("script_log_view", dpg.get_value("script_log_view") + f"\n--- ERROR ---\n{e}\n")
    finally:
        if SCRIPT_RUNNER_STATE.get("process") and SCRIPT_RUNNER_STATE["process"].stdout:
            SCRIPT_RUNNER_STATE["process"].stdout.close()
        SCRIPT_RUNNER_STATE["process"] = None
        SCRIPT_RUNNER_STATE["script_path"] = None
        dpg.configure_item("run_script_btn", enabled=True)
        dpg.hide_item("running_script_controls")
        dpg.configure_item("log_win_close_btn", enabled=True)

def run_orchestration_script():
    if SCRIPT_RUNNER_STATE.get("process"):
        dpg.configure_item("orc_msg", default_value="A script is already running.", color=[255,255,0]); return
    raw = dpg.get_value("script_combo")
    if not raw: dpg.configure_item("orc_msg", default_value="Select a script to run.", color=[255,0,0]); return
    script_path = resolve_script_path(raw)
    if not os.path.exists(script_path):
        dpg.configure_item("orc_msg", default_value="Script not found.", color=[255,0,0]); return
    dpg.set_value("script_log_view", "")
    dpg.configure_item("run_script_btn", enabled=False)
    dpg.show_item("running_script_controls")
    w, h = dpg.get_viewport_client_width(), dpg.get_viewport_client_height()
    dpg.configure_item("script_log_win", pos=(max(0, w//2 - LOG_W//2), max(0, h//2 - LOG_H//2)), show=True)
    dpg.configure_item("log_win_close_btn", enabled=True)
    threading.Thread(target=_execute_script_and_log, args=(script_path,), daemon=True).start()

def kill_script():
    proc = SCRIPT_RUNNER_STATE.get("process")
    if proc and proc.poll() is None:
        dpg.set_value("script_log_view", dpg.get_value("script_log_view") + "\n--- Sending kill signal... ---\n")
        try: os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except ProcessLookupError: pass
        except Exception as e:
            dpg.set_value("script_log_view", dpg.get_value("script_log_view") + f"\n--- ERROR during kill ---\n{e}\n")

# ─────────────────── MONITORING AND ARBITRARY PROCESS KILL ───────────────────
def _confirm_kill_callback(sender, app_data, user_data):
    """Callback from the confirmation dialog to robustly kill a process group."""
    dpg.configure_item("confirmation_kill_dialog", show=False)
    if not app_data:  # User clicked "Cancel"
        return

    pid_to_kill = int(user_data)
    try:
        # Get the Process Group ID from the main process ID
        pgid = os.getpgid(pid_to_kill)

        # Stage 1: Ask the entire process group to terminate nicely
        os.killpg(pgid, signal.SIGTERM)

        # Wait up to 2 seconds for the main process to terminate
        is_alive = True
        for _ in range(20):
            time.sleep(0.1)
            try:
                os.kill(pid_to_kill, 0)  # Check if the main process exists
            except ProcessLookupError:
                is_alive = False
                break

        # Stage 2: If the main process is still alive, force kill the entire group
        if is_alive:
            os.killpg(pgid, signal.SIGKILL)

    except ProcessLookupError:
        pass  # Process was already gone before we could do anything
    except Exception as e:
        print(f"Error killing process group for PID {pid_to_kill}: {e}")
    finally:
        # Refresh the UI after our best effort
        time.sleep(0.1)
        refresh_monitor_tab()


def kill_arbitrary_process(sender, app_data, user_data):
    """Opens a confirmation dialog before killing a specified process."""
    pid = user_data['pid']
    cmd = user_data['cmd']
    dpg.set_value("kill_confirm_text", f"Are you sure you want to kill this process?\n\nPID: {pid}\nCMD: {cmd}")
    dpg.configure_item("kill_confirm_ok_btn", user_data=pid)
    dpg.configure_item("confirmation_kill_dialog", show=True)

def monitor_loop():
    while dpg.is_dearpygui_running():
        if dpg.is_item_visible("monitoring_tab"):
            refresh_monitor_tab()
        time.sleep(MONITOR_SECS)

def refresh_monitor_tab():
    dpg.set_value("cron_view", get_crontab_text())
    sel=dpg.get_value("log_combo")
    if sel: dpg.set_value("log_view", tail_log(sel))

    dpg.delete_item("proc_table", children_only=True)
    dpg.add_table_column(label="Process Details", parent="proc_table")
    dpg.add_table_column(label="Action", width_fixed=True, width=80, parent="proc_table")

    processes = get_running_unison_processes()
    if not processes:
        with dpg.table_row(parent="proc_table"):
            dpg.add_text("(none)")
            dpg.add_text("")
    else:
        for proc in processes:
            with dpg.table_row(parent="proc_table"):
                dpg.add_text(f"PID: {proc['pid']} | {proc['cmd']}")
                kill_btn = dpg.add_button(label="Kill", user_data=proc, callback=kill_arbitrary_process)
                dpg.bind_item_theme(kill_btn, "kill_theme")

# ─────────────────────────── GUI LAYOUT ───────────────────────────
dpg.create_context()
dpg.create_viewport(title="Unison Orchestrator v3.18", width=1200, height=980)

with dpg.theme(tag="kill_theme"):
    with dpg.theme_component(dpg.mvButton):
        dpg.add_theme_color(dpg.mvThemeCol_Button, (220, 0, 0))
        dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (255, 50, 50))

with dpg.window(label="Unison Orchestrator v3.18", width=1180, height=960):
    dpg.add_button(label="Refresh Profiles", callback=lambda: refresh_profile_panel())
    dpg.add_spacer(height=6)
    with dpg.group(horizontal=True):
        with dpg.child_window(tag="profiles_panel", width=600, height=880, border=True): pass
        with dpg.child_window(width=550, height=880, border=True):
            with dpg.tab_bar():
                with dpg.tab(label="Create Profile"):
                    dpg.add_input_text(label="Profile Name", tag="new_name", width=480)
                    with dpg.group(horizontal=True):
                        dpg.add_input_text(label="Source Dir", tag="new_src", width=400)
                        dpg.add_button(label="Browse", callback=lambda: pick_directory("new_src"))
                    with dpg.group(horizontal=True):
                        dpg.add_input_text(label="Target Dir", tag="new_tgt", width=400)
                        dpg.add_button(label="Browse", callback=lambda: pick_directory("new_tgt"))
                    dpg.add_button(label="Save Profile", callback=save_new_profile)
                    dpg.add_text("", tag="new_msg")

                with dpg.tab(label="Orchestrator"):
                    dpg.add_button(label="Generate sync_all_profiles.sh", callback=generate_parallel_script)
                    dpg.add_separator()
                    dpg.add_text("Shell scripts referenced in crontab:")
                    dpg.add_combo([], tag="script_combo", width=380, callback=load_selected_script)
                    dpg.add_button(label="Refresh List", callback=refresh_script_combo)
                    dpg.add_input_text(tag="script_txt", multiline=True, width=500, height=260)
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="Save Script Changes", callback=save_script_changes)
                        dpg.add_button(label="Run Script Now", callback=run_orchestration_script, tag="run_script_btn")
                    with dpg.group(tag="running_script_controls", show=False, horizontal=True):
                        dpg.add_button(label="Show Log", callback=lambda: dpg.show_item("script_log_win"))
                        kill_btn = dpg.add_button(label="Kill Running Script", callback=kill_script)
                        dpg.bind_item_theme(kill_btn, "kill_theme")
                    dpg.add_separator()
                    dpg.add_input_text(label="Cron schedule", tag="cron_sched", width=200, default_value=DEFAULT_CRON, hint="min hr dom mon dow")
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="Install / Update Cron", callback=install_or_update_cron)
                        dpg.add_button(label="Remove Cron", callback=remove_cron_for_script)
                    dpg.add_text("", tag="orc_msg")

                with dpg.tab(label="Monitoring", tag="monitoring_tab"):
                    dpg.add_text("Cron entries:")
                    dpg.add_input_text(tag="cron_view", multiline=True, readonly=True, width=500, height=120)
                    dpg.add_separator()
                    dpg.add_text("Running Unison Processes:")
                    with dpg.table(header_row=True, tag="proc_table", resizable=True, policy=dpg.mvTable_SizingStretchProp):
                        dpg.add_table_column(label="Process Details")
                        dpg.add_table_column(label="Action", width_fixed=True, width=80)
                    dpg.add_separator()
                    dpg.add_text("Log viewer:")
                    dpg.add_combo(load_profiles(), tag="log_combo", width=250, callback=lambda: refresh_monitor_tab())
                    dpg.add_input_text(tag="log_view", multiline=True, readonly=True, width=500, height=220)

with dpg.window(label="Confirm Kill", modal=True, show=False, id="confirmation_kill_dialog", no_title_bar=True, pos=(400,400)):
    dpg.add_text("Are you sure?", tag="kill_confirm_text")
    dpg.add_separator()
    with dpg.group(horizontal=True):
        dpg.add_button(label="OK", width=75, callback=_confirm_kill_callback, id="kill_confirm_ok_btn")
        dpg.add_button(label="Cancel", width=75, callback=lambda: dpg.configure_item("confirmation_kill_dialog", show=False))

with dpg.window(label="Edit Profile", tag="edit_win", width=EDIT_W, height=EDIT_H, pos=(0,0), show=False):
    dpg.add_input_text(label="Profile Name", tag="edit_name", width=500, enabled=False)
    with dpg.group(horizontal=True):
        dpg.add_input_text(label="Source Dir", tag="edit_src", width=400); dpg.add_button(label="Browse", callback=lambda: pick_directory("edit_src"))
    with dpg.group(horizontal=True):
        dpg.add_input_text(label="Target Dir", tag="edit_tgt", width=400); dpg.add_button(label="Browse", callback=lambda: pick_directory("edit_tgt"))
    dpg.add_button(label="Save Changes", callback=save_profile_edit)
    dpg.add_text("", tag="edit_msg")
with dpg.window(label="Script Output", tag="script_log_win", width=LOG_W, height=LOG_H, show=False, modal=False, no_close=True):
    dpg.add_input_text(tag="script_log_view", multiline=True, readonly=True, width=-1, height=-40)
    dpg.add_button(label="Hide Log", tag="log_win_close_btn", width=-1, callback=lambda: dpg.hide_item("script_log_win"))
with dpg.file_dialog(directory_selector=True, show=False, tag="dlg_new_src", callback=lambda s,a,u: _set_value_from_dialog(s,a,"new_src")): dpg.add_file_extension(".*")
with dpg.file_dialog(directory_selector=True, show=False, tag="dlg_new_tgt", callback=lambda s,a,u: _set_value_from_dialog(s,a,"new_tgt")): dpg.add_file_extension(".*")
with dpg.file_dialog(directory_selector=True, show=False, tag="dlg_edit_src", callback=lambda s,a,u: _set_value_from_dialog(s,a,"edit_src")): dpg.add_file_extension(".*")
with dpg.file_dialog(directory_selector=True, show=False, tag="dlg_edit_tgt", callback=lambda s,a,u: _set_value_from_dialog(s,a,"edit_tgt")): dpg.add_file_extension(".*")

# ───────────────────────── STARTUP ─────────────────────────
refresh_profile_panel()
refresh_script_combo()
dpg.setup_dearpygui()
dpg.show_viewport()
threading.Thread(target=monitor_loop, daemon=True).start()
dpg.start_dearpygui()
dpg.destroy_context()