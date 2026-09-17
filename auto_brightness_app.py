import sys
import os
import json
import time
import logging
import threading
from queue import Queue
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox
import cv2
import screen_brightness_control as sbc
from PIL import Image, ImageDraw
import pystray
import collections

def get_app_data_dir():
    # Targets C:\Users\YourName\AppData\Local\AutoBrightness
    app_data = os.getenv('LOCALAPPDATA') or os.path.expanduser('~')
    app_dir = os.path.join(app_data, 'AutoBrightness')
    os.makedirs(app_dir, exist_ok=True)
    return app_dir

APP_DIR = get_app_data_dir()
CONFIG_FILE = os.path.join(APP_DIR, "auto_brightness_config.json")
LOG_FILE = os.path.join(APP_DIR, "auto_brightness.log")

# Set up robust local logging
logging.basicConfig(
    filename="auto_brightness.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(threadName)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

CONFIG_FILE = "auto_brightness_config.json"

def get_resource_path(relative_path):
    """Get absolute path to resource for cx_Freeze or raw Python."""
    if getattr(sys, 'frozen', False):
        # The application is installed and running compiled
        application_path = os.path.dirname(sys.executable)
    else:
        # The application is running as a raw Python script
        application_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(application_path, relative_path)

class AutoBrightnessApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Advanced Dual Display Auto-Brightness Sync")
        self.root.geometry("1000x800") # Expanded for new buttons
        self.root.resizable(True, True)
        self.root.iconbitmap(get_resource_path('logo.ico'))

        # Thread Synchronization Tools
        self.lock = threading.Lock()
        self.shutdown_event = threading.Event()
        self.ui_update_queue = Queue()
        
        self._programmatic_slider_update = False
        
        # Plain Python Thread-Safe State Cache Mirror
        self.shared_state = {
            "auto_enabled": True,
            "manual_override": False,
            "m1_manual_val": 50,
            "m2_manual_val": 50,
            "smoothing_alpha": 0.15,
            "hysteresis_db": 3.0,
            "sample_interval": 4
        }
        
        # Tkinter UI Binding Variables
        self.auto_enabled_var = tk.BooleanVar(value=True)
        self.manual_override_var = tk.BooleanVar(value=False)
        self.start_minimized_var = tk.BooleanVar(value=False)
        self.windows_startup_var = tk.BooleanVar(value=False)
        
        self.smoothing_alpha_var = tk.DoubleVar(value=0.15)
        self.hysteresis_db_var = tk.DoubleVar(value=3.0)
        self.sample_interval_var = tk.IntVar(value=4)
        
        # Live Operational Telemetry Variables
        self.current_ambient_raw = 0.0
        self.current_ambient_smoothed = 0.0
        
        # Fader Loop Variables (Smooth Transitions)
        self.fader_target_m1 = 50
        self.fader_target_m2 = 50
        self.current_actual_m1 = 50
        self.current_actual_m2 = 50
        
        self.camera_status_str = "Initializing..."
        self.monitor_status_str = "Scanning..."
        self.last_updated_str = "Never"
        
        # Configuration Struct holding the 5 Mapping Condition Nodes
        self.presets = {
            "Highest":  {"ambient": 80.0, "m1": 100, "m2": 100, "use": tk.BooleanVar(value=True)},
            "Moderate": {"ambient": 50.0, "m1": 70,  "m2": 70,  "use": tk.BooleanVar(value=True)},
            "Low":      {"ambient": 30.0, "m1": 45,  "m2": 45,  "use": tk.BooleanVar(value=False)},
            "Lower":    {"ambient": 15.0, "m1": 25,  "m2": 25,  "use": tk.BooleanVar(value=False)},
            "Lowest":   {"ambient": 5.0,  "m1": 10,  "m2": 10,  "use": tk.BooleanVar(value=True)}
        }
        
        # Sync Initial Brightness levels
        try:
            curr_b = sbc.get_brightness()
            if len(curr_b) > 0: self.current_actual_m1 = curr_b[0]
            if len(curr_b) > 1: self.current_actual_m2 = curr_b[1]
        except:
            pass

        # Safe Initialization Routines
        self.load_configuration_file()
        self.sync_tk_vars_to_shared_state()
        self.build_advanced_ui_dashboard()
        
        # Windows System Handling Hooks
        self.root.protocol('WM_DELETE_WINDOW', self.minimize_to_tray)
        
        # Fire background automated background service engine (Camera Poller)
        self.worker_thread = threading.Thread(target=self.core_sensor_engine, name="CoreSensorWorker", daemon=True)
        self.worker_thread.start()

        # Fire background fader thread for smooth brightness transitions
        self.fader_thread = threading.Thread(target=self.smooth_fader_engine, name="SmoothFaderWorker", daemon=True)
        self.fader_thread.start()
        
        # Run local GUI safe asynchronous polling loop
        self.process_ui_queue_loop()
        
        if self.start_minimized_var.get():
            self.root.after(100, self.minimize_to_tray)

    def sync_tk_vars_to_shared_state(self):
        with self.lock:
            self.shared_state["auto_enabled"] = self.auto_enabled_var.get()
            self.shared_state["manual_override"] = self.manual_override_var.get()
            self.shared_state["smoothing_alpha"] = self.smoothing_alpha_var.get()
            self.shared_state["hysteresis_db"] = self.hysteresis_db_var.get()
            self.shared_state["sample_interval"] = self.sample_interval_var.get()

    def load_configuration_file(self):
        if not os.path.exists(CONFIG_FILE):
            return
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
            self.auto_enabled_var.set(data.get("auto_enabled", True))
            self.start_minimized_var.set(data.get("start_minimized", False))
            self.windows_startup_var.set(data.get("windows_startup", False))
            self.smoothing_alpha_var.set(data.get("smoothing_alpha", 0.15))
            self.hysteresis_db_var.set(data.get("hysteresis_db", 3.0))
            self.sample_interval_var.set(data.get("sample_interval", 4))
            saved_presets = data.get("presets", {})
            for level, metrics in saved_presets.items():
                if level in self.presets:
                    self.presets[level]["ambient"] = float(metrics.get("ambient", self.presets[level]["ambient"]))
                    self.presets[level]["m1"] = int(metrics.get("m1", self.presets[level]["m1"]))
                    self.presets[level]["m2"] = int(metrics.get("m2", self.presets[level]["m2"]))
                    self.presets[level]["use"].set(metrics.get("use", self.presets[level]["use"].get()))
        except Exception as e:
            logging.error(f"Failed parsing saved options JSON mapping: {str(e)}")

    def serialize_and_save_config(self):
        try:
            out = {
                "auto_enabled": self.auto_enabled_var.get(),
                "start_minimized": self.start_minimized_var.get(),
                "windows_startup": self.windows_startup_var.get(),
                "smoothing_alpha": self.smoothing_alpha_var.get(),
                "hysteresis_db": self.hysteresis_db_var.get(),
                "sample_interval": self.sample_interval_var.get(),
                "presets": {}
            }
            for level, items in self.presets.items():
                out["presets"][level] = {
                    "ambient": items["ambient"],
                    "m1": items["m1"],
                    "m2": items["m2"],
                    "use": items["use"].get()
                }
            with open(CONFIG_FILE, "w") as f:
                json.dump(out, f, indent=4)
        except Exception as e:
            logging.error(f"Failed exporting system state to local cache: {str(e)}")

    def build_advanced_ui_dashboard(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=10)
        
        tab_dash = ttk.Frame(self.notebook, padding=10)
        tab_config = ttk.Frame(self.notebook, padding=10)
        tab_hardware = ttk.Frame(self.notebook, padding=10)
        tab_settings = ttk.Frame(self.notebook, padding=10)
        
        self.notebook.add(tab_dash, text=" Telemetry Dashboard ")
        self.notebook.add(tab_config, text=" Brightness Step Profiles ")
        self.notebook.add(tab_hardware, text=" Display & Hardware Config ")
        self.notebook.add(tab_settings, text=" Engine Settings ")

        # ==================== TAB 1: TELEMETRY DASHBOARD ====================
        status_frame = ttk.LabelFrame(tab_dash, text=" Live Hardware Telemetry Center ", padding=15)
        status_frame.pack(fill="x", padx=5, pady=5)
        
        labels = [
            ("Camera Capture Status:", "lbl_cam_stat", "#000000"),
            ("Display Connection State:", "lbl_mon_stat", "#000000"),
            ("Smoothed Sensor Energy:", "lbl_amb_sm", "#0078D4"),
            ("Target Brightness Monitor 1:", "lbl_m1_act", "#E81123"),
            ("Target Brightness Monitor 2:", "lbl_m2_act", "#E81123"),
        ]
        
        for idx, (text, var_name, color) in enumerate(labels):
            ttk.Label(status_frame, text=text, font=("Arial", 10)).grid(row=idx, column=0, sticky="w", pady=4)
            lbl = ttk.Label(status_frame, text="--", font=("Arial", 10, "bold"), foreground=color)
            lbl.grid(row=idx, column=1, sticky="w", padx=15, pady=4)
            setattr(self, var_name, lbl)
            
        ctl_frame = ttk.LabelFrame(tab_dash, text=" Live Automation Override Switchboard ", padding=15)
        ctl_frame.pack(fill="both", expand=True, padx=5, pady=10)
        
        ttk.Checkbutton(ctl_frame, text="Enable Automated Calibration Control Loop", variable=self.auto_enabled_var, 
                        command=lambda: [self.sync_tk_vars_to_shared_state(), self.serialize_and_save_config()]).pack(anchor="w", pady=5)
        ttk.Checkbutton(ctl_frame, text="Force Manual Brightness Override Hold", variable=self.manual_override_var, 
                        command=self.sync_tk_vars_to_shared_state).pack(anchor="w", pady=5)
        
        self.m1_slider_lbl = ttk.Label(ctl_frame, text="Manual Monitor 1 Override Level: -- %")
        self.m1_slider_lbl.pack(anchor="w", pady=(10, 2))
        self.m1_override_scale = ttk.Scale(ctl_frame, from_=0, to=100, orient="horizontal", command=self.trigger_manual_override_m1)
        self.m1_override_scale.pack(fill="x", pady=2)
        
        self.m2_slider_lbl = ttk.Label(ctl_frame, text="Manual Monitor 2 Override Level: -- %")
        self.m2_slider_lbl.pack(anchor="w", pady=(10, 2))
        self.m2_override_scale = ttk.Scale(ctl_frame, from_=0, to=100, orient="horizontal", command=self.trigger_manual_override_m2)
        self.m2_override_scale.pack(fill="x", pady=2)

        # ==================== TAB 2: BRIGHTNESS STEP PROFILES ====================
        matrix_frame = ttk.LabelFrame(tab_config, text=" Matrix Profiler Mapping Matrix (Min 2 Rows Active) ", padding=10)
        matrix_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        headers = ["Active", "Profile", "Ambient %", "Disp 1 %", "Disp 2 %", "Actions"]
        for col_idx, text in enumerate(headers):
            ttk.Label(matrix_frame, text=text, font=("Arial", 9, "bold")).grid(row=0, column=col_idx, padx=6, pady=6)
            
        self.ui_inputs = {}
        for row_idx, (level, data) in enumerate(self.presets.items(), start=1):
            chk = ttk.Checkbutton(matrix_frame, variable=data["use"])
            chk.grid(row=row_idx, column=0, padx=6, pady=6)
            ttk.Label(matrix_frame, text=level, font=("Arial", 9, "bold")).grid(row=row_idx, column=1, padx=6, pady=6, sticky="w")
            
            amb_ent = ttk.Entry(matrix_frame, width=8, justify="center")
            amb_ent.insert(0, f"{data['ambient']:.1f}")
            amb_ent.grid(row=row_idx, column=2, padx=6, pady=6)
            
            m1_ent = ttk.Entry(matrix_frame, width=8, justify="center")
            m1_ent.insert(0, str(data["m1"]))
            m1_ent.grid(row=row_idx, column=3, padx=6, pady=6)
            
            m2_ent = ttk.Entry(matrix_frame, width=8, justify="center")
            m2_ent.insert(0, str(data["m2"]))
            m2_ent.grid(row=row_idx, column=4, padx=6, pady=6)
            
            self.ui_inputs[level] = {"amb": amb_ent, "m1": m1_ent, "m2": m2_ent, "use": data["use"]}
            
            # Action Buttons
            btn_frame = ttk.Frame(matrix_frame)
            btn_frame.grid(row=row_idx, column=5, padx=6, pady=6)
            
            ttk.Button(btn_frame, text="Sense", width=6, 
                       command=lambda l=level: self.action_sense_ambient(l)).pack(side="left", padx=2)
            ttk.Button(btn_frame, text="Set", width=6, 
                       command=lambda l=level: self.action_set_brightness_popup(l)).pack(side="left", padx=2)
            ttk.Button(btn_frame, text="Save", width=6, 
                       command=lambda l=level: self.action_save_row(l)).pack(side="left", padx=2)

        ttk.Button(tab_config, text="Commit All Profiles to Disk", command=self.validate_and_commit_inputs).pack(anchor="e", pady=10)

        # ==================== TAB 3: DISPLAY CONFIGURATION ====================
        hw_frame = ttk.LabelFrame(tab_hardware, text=" Hardware Brightness Methodology Engine ", padding=15)
        hw_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        ttk.Label(hw_frame, text="screen_brightness_control automatically determines the best method for your displays.\n"
                                 "VCP = Hardware DDC/CI (External Monitors)\n"
                                 "WMI = Windows Management/Intel HD Graphics Color Intensity (Laptops & Software Fallback)", 
                                 foreground="#555555").pack(anchor="w", pady=(0, 10))
                                 
        self.display_info_text = tk.Text(hw_frame, height=10, state="disabled", font=("Courier", 9))
        self.display_info_text.pack(fill="both", expand=True, pady=5)
        
        ttk.Button(hw_frame, text="Detect & Refresh Hardware Capabilities", command=self.refresh_display_hardware_info).pack(pady=10)
        self.refresh_display_hardware_info()

        # ==================== TAB 4: ENGINE SETTINGS ====================
        eng_frame = ttk.LabelFrame(tab_settings, text=" Control Loop Math and Operational Fine-Tuning ", padding=15)
        eng_frame.pack(fill="x", padx=5, pady=5)
        
        ttk.Label(eng_frame, text="Exponential Smoothing Alpha Factor (0.01 - 1.0):").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Entry(eng_frame, textvariable=self.smoothing_alpha_var, width=12).grid(row=0, column=1, sticky="w", padx=10)
        
        ttk.Label(eng_frame, text="Webcam Frame Polling Delay Rate (Seconds):").grid(row=2, column=0, sticky="w", pady=6)
        ttk.Entry(eng_frame, textvariable=self.sample_interval_var, width=12).grid(row=2, column=1, sticky="w", padx=10)
        
        boot_frame = ttk.LabelFrame(tab_settings, text=" Operational Environment Hooks ", padding=15)
        boot_frame.pack(fill="both", expand=True, padx=5, pady=10)
        ttk.Checkbutton(boot_frame, text="Initialize Minimized in System Tray", variable=self.start_minimized_var, command=self.serialize_and_save_config).pack(anchor="w", pady=5)
        ttk.Checkbutton(boot_frame, text="Launch Application at Windows Startup", variable=self.windows_startup_var, command=self.handle_windows_startup_hook).pack(anchor="w", pady=5)

    # ------------------ PROFILE TAB ACTIONS ------------------

    def action_sense_ambient(self, level):
        """Reads current ambient light and fills the Ambient Entry for this row."""
        current_amb = self.current_ambient_smoothed
        self.ui_inputs[level]["amb"].delete(0, tk.END)
        self.ui_inputs[level]["amb"].insert(0, f"{current_amb:.1f}")

    def action_set_brightness_popup(self, level):
        """Opens a live-tuning slider popup to set comfortable brightness for a profile."""
        popup = tk.Toplevel(self.root)
        popup.title(f"Live Tune: {level} Profile")
        popup.geometry("400x250")
        popup.grab_set() # Focus lock
        
        # Temporarily enable manual override so fader respects these sliders
        self.manual_override_var.set(True)
        self.sync_tk_vars_to_shared_state()
        
        ttk.Label(popup, text=f"Adjust sliders to find comfortable brightness for '{level}'", font=("Arial", 9, "bold")).pack(pady=10)
        
        # M1 Setup
        m1_val = tk.IntVar(value=int(self.ui_inputs[level]["m1"].get() or 50))
        ttk.Label(popup, text="Display 1:").pack()
        m1_scale = ttk.Scale(popup, from_=0, to=100, orient="horizontal", variable=m1_val, 
                             command=lambda val: self._live_update_target(1, float(val)))
        m1_scale.pack(fill="x", padx=20, pady=5)
        
        # M2 Setup
        m2_val = tk.IntVar(value=int(self.ui_inputs[level]["m2"].get() or 50))
        ttk.Label(popup, text="Display 2:").pack()
        m2_scale = ttk.Scale(popup, from_=0, to=100, orient="horizontal", variable=m2_val, 
                             command=lambda val: self._live_update_target(2, float(val)))
        m2_scale.pack(fill="x", padx=20, pady=5)
        
        def save_and_close():
            self.ui_inputs[level]["m1"].delete(0, tk.END)
            self.ui_inputs[level]["m1"].insert(0, str(m1_val.get()))
            self.ui_inputs[level]["m2"].delete(0, tk.END)
            self.ui_inputs[level]["m2"].insert(0, str(m2_val.get()))
            self.manual_override_var.set(False) # Turn off manual override to resume auto
            self.sync_tk_vars_to_shared_state()
            popup.destroy()
            
        ttk.Button(popup, text="Apply to Profile", command=save_and_close).pack(pady=20)
        
    def _live_update_target(self, monitor, val):
        """Live updates the thread state so the fader engine immediately adjusts the screen."""
        with self.lock:
            if monitor == 1:
                self.shared_state["m1_manual_val"] = int(val)
            else:
                self.shared_state["m2_manual_val"] = int(val)

    def action_save_row(self, level):
        """Saves just this specific row to the active struct."""
        try:
            amb = float(self.ui_inputs[level]["amb"].get())
            m1 = int(self.ui_inputs[level]["m1"].get())
            m2 = int(self.ui_inputs[level]["m2"].get())
            with self.lock:
                self.presets[level]["ambient"] = amb
                self.presets[level]["m1"] = m1
                self.presets[level]["m2"] = m2
            self.serialize_and_save_config()
            messagebox.showinfo("Saved", f"Profile '{level}' updated successfully.")
        except ValueError:
            messagebox.showerror("Error", "Please ensure numbers are formatted correctly.")

    # ------------------ HARDWARE INFO ENGINE ------------------

    def refresh_display_hardware_info(self):
        """Interrogates SBC to figure out what dimming method is being used per display."""
        self.display_info_text.config(state="normal")
        self.display_info_text.delete(1.0, tk.END)
        try:
            monitors = sbc.list_monitors_info()
            if not monitors:
                self.display_info_text.insert(tk.END, "No controllable monitors detected by SBC.")
            else:
                for idx, mon in enumerate(monitors):
                    name = mon.get("name", "Generic Monitor")
                    method = mon.get("method", "Unknown")
                    self.display_info_text.insert(tk.END, f"Display {idx + 1}: {name}\n")
                    self.display_info_text.insert(tk.END, f"  -> Active Method: {method}\n")
                    if method == "WMI":
                        self.display_info_text.insert(tk.END, f"  -> Note: Using Intel/Software Color Intensity Fallback.\n")
                    elif method == "VCP":
                        self.display_info_text.insert(tk.END, f"  -> Note: Using Hardware DDC/CI Protocol.\n")
                    self.display_info_text.insert(tk.END, "-"*40 + "\n")
        except Exception as e:
            self.display_info_text.insert(tk.END, f"Error detecting monitors: {str(e)}")
        self.display_info_text.config(state="disabled")

    # ------------------ CORE LOGIC AND THREADS ------------------

    def trigger_manual_override_m1(self, event=None):
        if self._programmatic_slider_update: return
        if not self.manual_override_var.get(): self.manual_override_var.set(True)
        m1_val = int(self.m1_override_scale.get())
        self.m1_slider_lbl.config(text=f"Manual Monitor 1 Override Level: {m1_val} %")
        with self.lock:
            self.shared_state["manual_override"] = True
            self.shared_state["m1_manual_val"] = m1_val

    def trigger_manual_override_m2(self, event=None):
        if self._programmatic_slider_update: return
        if not self.manual_override_var.get(): self.manual_override_var.set(True)
        m2_val = int(self.m2_override_scale.get())
        self.m2_slider_lbl.config(text=f"Manual Monitor 2 Override Level: {m2_val} %")
        with self.lock:
            self.shared_state["manual_override"] = True
            self.shared_state["m2_manual_val"] = m2_val

    def handle_windows_startup_hook(self):
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        app_name = "DualDisplayAutoBrightness"
        exe_path = f'"{sys.executable}" "{os.path.abspath(__file__)}"'
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
            if self.windows_startup_var.get():
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, exe_path)
            else:
                try: winreg.DeleteValue(key, app_name)
                except FileNotFoundError: pass
            winreg.CloseKey(key)
            self.serialize_and_save_config()
        except Exception as e:
            messagebox.showerror("Error", f"Registry error:\n{str(e)}")

    def validate_and_commit_inputs(self):
        with self.lock:
            for level in self.presets.keys():
                self.presets[level]["ambient"] = float(self.ui_inputs[level]["amb"].get())
                self.presets[level]["m1"] = int(self.ui_inputs[level]["m1"].get())
                self.presets[level]["m2"] = int(self.ui_inputs[level]["m2"].get())
        self.sync_tk_vars_to_shared_state()
        self.serialize_and_save_config()
        messagebox.showinfo("Success", "All mapping profiles successfully committed.")

    def resolve_interpolation_profiles(self, ambient_energy):
        points = []
        with self.lock:
            for level, items in self.presets.items():
                if items["use"].get():
                    points.append({"amb": items["ambient"], "m1": items["m1"], "m2": items["m2"]})
        points = sorted(points, key=lambda x: x["amb"])
        if len(points) < 2: return 50, 50
        if ambient_energy <= points[0]["amb"]: return points[0]["m1"], points[0]["m2"]
        if ambient_energy >= points[-1]["amb"]: return points[-1]["m1"], points[-1]["m2"]
            
        for i in range(len(points) - 1):
            p1 = points[i]
            p2 = points[i+1]
            if p1["amb"] <= ambient_energy <= p2["amb"]:
                denominator = p2["amb"] - p1["amb"]
                if abs(denominator) < 1e-5: return p1["m1"], p1["m2"]
                scalar = (ambient_energy - p1["amb"]) / denominator
                target_m1 = p1["m1"] + scalar * (p2["m1"] - p1["m1"])
                target_m2 = p1["m2"] + scalar * (p2["m2"] - p1["m2"])
                return int(target_m1), int(target_m2)
        return 50, 50

    def smooth_fader_engine(self):
        """Thread that safely steps brightness without saturating the hardware bus."""
        logging.info("Hardware-Safe Fader Thread initialized.")
        
        while not self.shutdown_event.is_set():
            try:
                # 1. Dynamically fetch explicit monitor IDs to prevent WMI/DDC routing confusion
                monitors = sbc.list_monitors()
                mon_count = len(monitors)
                
                # Step by 4% to reduce bus traffic, preventing command queuing/flickering
                step_size = 4 
                
                # Update Display 1 using explicit hardware name
                if mon_count > 0:
                    mon1_name = monitors[0]
                    if self.current_actual_m1 != self.fader_target_m1 and self.fader_target_m1 != -1:
                        diff = self.fader_target_m1 - self.current_actual_m1
                        if abs(diff) <= step_size:
                            self.current_actual_m1 = self.fader_target_m1
                        else:
                            self.current_actual_m1 += step_size if diff > 0 else -step_size
                            
                        try: sbc.set_brightness(self.current_actual_m1, display=mon1_name)
                        except: pass

                # Update Display 2 using explicit hardware name
                if mon_count > 1:
                    mon2_name = monitors[1]
                    if self.current_actual_m2 != self.fader_target_m2 and self.fader_target_m2 != -1:
                        diff = self.fader_target_m2 - self.current_actual_m2
                        if abs(diff) <= step_size:
                            self.current_actual_m2 = self.fader_target_m2
                        else:
                            self.current_actual_m2 += step_size if diff > 0 else -step_size
                            
                        try: sbc.set_brightness(self.current_actual_m2, display=mon2_name)
                        except: pass

            except Exception as e:
                logging.error(f"Fader engine hardware routing error: {e}")
                
            # 150ms sleep gives the I2C bus/WMI pipeline enough time to process the command
            time.sleep(0.15)

    def core_sensor_engine(self):
        """Fast-looping thread that continuously averages light over the last 5 seconds."""
        logging.info("Continuous Ambient Sensor Pipeline Initialized.")
        camera_id = 0
        cap = cv2.VideoCapture(camera_id, cv2.CAP_DSHOW)
        
        # Stores exactly 10 samples. Polling every 0.5s creates a perfect 5-second moving average.
        ambient_history = collections.deque(maxlen=10)
        
        while not self.shutdown_event.is_set():
            loop_start_time = time.time()
            
            with self.lock:
                auto_enabled = self.shared_state["auto_enabled"]
                manual_override = self.shared_state["manual_override"]
                m1_manual_val = self.shared_state["m1_manual_val"]
                m2_manual_val = self.shared_state["m2_manual_val"]
                
            # Camera Read & Exposure Lock
            if not cap.isOpened(): 
                cap.open(camera_id, cv2.CAP_DSHOW)
                cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
                
            if cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None:
                    self.camera_status_str = "Online (Continuous Sensing)"
                    h, w, _ = frame.shape
                    
                    # Top 30% of the frame (ambient ceiling/room light)
                    roi = frame[0:int(h*0.30), 0:w]
                    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
                    collapsed_pixel = cv2.resize(gray, (1, 1))
                    
                    raw_val = float((collapsed_pixel / 255.0) * 100.0)
                    ambient_history.append(raw_val)
                    
                    # Calculate the true average of the past 5 seconds
                    self.current_ambient_smoothed = sum(ambient_history) / len(ambient_history)
                else:
                    self.camera_status_str = "Error (Capture Dropping)"
            
            try:
                monitors = sbc.list_monitors()
                self.monitor_status_str = f"Online ({len(monitors)} Monitors Synced)"
            except:
                self.monitor_status_str = "Scan Error"
                
            # Target Calculation
            if manual_override:
                target_m1, target_m2 = m1_manual_val, m2_manual_val
            elif auto_enabled:
                target_m1, target_m2 = self.resolve_interpolation_profiles(self.current_ambient_smoothed)
            else:
                target_m1, target_m2 = -1, -1

            # Dispatch to Fader Thread
            self.fader_target_m1 = target_m1
            self.fader_target_m2 = target_m2

            telemetry_snapshot = {
                "cam": self.camera_status_str,
                "mon": self.monitor_status_str,
                "sm": self.current_ambient_smoothed,
                "m1": self.fader_target_m1,
                "m2": self.fader_target_m2,
                "auto_enabled": auto_enabled,
                "manual_override": manual_override
            }
            self.ui_update_queue.put(telemetry_snapshot)
            
            # Continuously poll the camera exactly every 0.5 seconds
            elapsed = time.time() - loop_start_time
            self.shutdown_event.wait(timeout=max(0.05, 0.5 - elapsed))
            
        cap.release()

    def smooth_fader_engine(self):
        """Thread that steps brightness strictly by 1% for ultra-smooth transitions."""
        logging.info("Continuous 1% Step Fader Thread initialized.")
        
        while not self.shutdown_event.is_set():
            try:
                monitors = sbc.list_monitors()
                mon_count = len(monitors)
                
                # Update Display 1 (Strict 1% steps)
                if mon_count > 0:
                    mon1_name = monitors[0]
                    if self.current_actual_m1 != self.fader_target_m1 and self.fader_target_m1 != -1:
                        if self.current_actual_m1 < self.fader_target_m1:
                            self.current_actual_m1 += 1
                        else:
                            self.current_actual_m1 -= 1
                            
                        try: sbc.set_brightness(self.current_actual_m1, display=mon1_name)
                        except: pass

                # Update Display 2 (Strict 1% steps)
                if mon_count > 1:
                    mon2_name = monitors[1]
                    if self.current_actual_m2 != self.fader_target_m2 and self.fader_target_m2 != -1:
                        if self.current_actual_m2 < self.fader_target_m2:
                            self.current_actual_m2 += 1
                        else:
                            self.current_actual_m2 -= 1
                            
                        try: sbc.set_brightness(self.current_actual_m2, display=mon2_name)
                        except: pass

            except Exception as e:
                pass
                
            # Sleep 80ms between 1% steps. 
            # A huge change from 20% to 100% will now take a smooth ~6.5 seconds.
            time.sleep(0.08)

    def process_ui_queue_loop(self):
        while not self.ui_update_queue.empty():
            try:
                data = self.ui_update_queue.get_nowait()
                self.lbl_cam_stat.config(text=data["cam"])
                self.lbl_mon_stat.config(text=data["mon"])
                self.lbl_amb_sm.config(text=f"{data['sm']:.2f} %")
                self.lbl_m1_act.config(text=f"{data['m1']} % (Fading...)" if data['m1'] != -1 else "Suspended Mode")
                self.lbl_m2_act.config(text=f"{data['m2']} % (Fading...)" if data['m2'] != -1 else "Suspended Mode")
                
                self.auto_enabled_var.set(data["auto_enabled"])
                self.manual_override_var.set(data["manual_override"])
                
                if not data["manual_override"]:
                    self._programmatic_slider_update = True
                    self.m1_override_scale.set(data["m1"] if data["m1"] != -1 else 50)
                    self.m2_override_scale.set(data["m2"] if data["m2"] != -1 else 50)
                    self._programmatic_slider_update = False
                    
                self.m1_slider_lbl.config(text=f"Manual Monitor 1 Target: {data['m1']} % (Auto Tracking)")
                self.m2_slider_lbl.config(text=f"Manual Monitor 2 Target: {data['m2']} % (Auto Tracking)")
            except Exception: pass
        self.root.after(200, self.process_ui_queue_loop)

    def minimize_to_tray(self):
        self.root.withdraw()
        image = Image.open(get_resource_path('logo.ico'))
        draw = ImageDraw.Draw(image)
        draw.ellipse((16, 16, 48, 48), fill=(255, 255, 255))
        menu = pystray.Menu(
            pystray.MenuItem("Open Configuration Dashboard", self.restore_from_tray, default=True),
            pystray.MenuItem("Exit System Utility Services", self.quit_application)
        )
        self.tray_icon = pystray.Icon("AutoBrightEngine", image, "Auto Brightness", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def restore_from_tray(self):
        if hasattr(self, 'tray_icon') and self.tray_icon: self.tray_icon.stop()
        self.root.deiconify()

    def quit_application(self):
        self.shutdown_event.set()
        if hasattr(self, 'tray_icon') and self.tray_icon: self.tray_icon.stop()
        self.root.quit()
        sys.exit(0)


if __name__ == "__main__":
    root = tk.Tk()
    app = AutoBrightnessApp(root)
    root.mainloop()