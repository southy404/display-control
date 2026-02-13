from __future__ import annotations
import ctypes
from ctypes import wintypes
import threading
import time
import customtkinter as ctk
import screen_brightness_control as sbc
import os
import sys  # WICHTIG: Für resource_path notwendig
from PIL import Image

# ==========================================================
# HILFSFUNKTION FÜR PYINSTALLER
# ==========================================================
def resource_path(relative_path):
    """ Ermittelt den absoluten Pfad zu Ressourcen für PyInstaller """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# ==========================================================
# CTYPES & BACKEND SETUP
# ==========================================================

ctk.set_appearance_mode("dark")
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

class MonitorNameFetcher:
    """Holt den 'Friendly Name' aus der EDID via Windows API."""
    def get_real_names(self) -> list[str]:
        names = []
        try:
            QDC_ONLY_ACTIVE_PATHS = 0x00000002
            DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME = 2
            
            class LUID(ctypes.Structure):
                _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", ctypes.c_long)]

            class DISPLAYCONFIG_PATH_SOURCE_INFO(ctypes.Structure):
                _fields_ = [("adapterId", LUID), ("id", wintypes.DWORD), ("modeInfoIdx", ctypes.c_uint32), ("statusFlags", ctypes.c_uint32)]

            class DISPLAYCONFIG_PATH_TARGET_INFO(ctypes.Structure):
                _fields_ = [("adapterId", LUID), ("id", wintypes.DWORD), ("modeInfoIdx", ctypes.c_uint32),
                            ("outputTechnology", ctypes.c_uint32), ("rotation", ctypes.c_uint32), ("scaling", ctypes.c_uint32),
                            ("refreshRate", ctypes.c_uint32 * 2), ("scanLineOrdering", ctypes.c_uint32), ("targetAvailable", ctypes.c_int), ("statusFlags", ctypes.c_uint32)]

            class DISPLAYCONFIG_PATH_INFO(ctypes.Structure):
                _fields_ = [("sourceInfo", DISPLAYCONFIG_PATH_SOURCE_INFO), ("targetInfo", DISPLAYCONFIG_PATH_TARGET_INFO), ("flags", ctypes.c_uint32)]

            class DISPLAYCONFIG_MODE_INFO(ctypes.Structure):
                _fields_ = [("infoType", ctypes.c_uint32), ("id", ctypes.c_uint32), ("adapterId", LUID), ("modeInfo", ctypes.c_byte * 64)]

            class DISPLAYCONFIG_TARGET_DEVICE_NAME(ctypes.Structure):
                _fields_ = [
                    ("header", ctypes.c_byte * 20),
                    ("flags", ctypes.c_uint32),
                    ("outputTechnology", ctypes.c_uint32),
                    ("edidManufactureId", ctypes.c_uint16),
                    ("edidProductCodeId", ctypes.c_uint16),
                    ("connectorInstance", ctypes.c_uint32),
                    ("monitorFriendlyDeviceName", wintypes.WCHAR * 64),
                    ("monitorDevicePath", wintypes.WCHAR * 128)
                ]

            num_paths = ctypes.c_uint32(0)
            num_modes = ctypes.c_uint32(0)
            
            ctypes.windll.user32.GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(num_paths), ctypes.byref(num_modes))
            paths = (DISPLAYCONFIG_PATH_INFO * num_paths.value)()
            modes = (DISPLAYCONFIG_MODE_INFO * num_modes.value)()
            
            if ctypes.windll.user32.QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(num_paths), paths, ctypes.byref(num_modes), modes, None) == 0:
                for i in range(num_paths.value):
                    target_name = DISPLAYCONFIG_TARGET_DEVICE_NAME()
                    struct_size = ctypes.sizeof(DISPLAYCONFIG_TARGET_DEVICE_NAME)
                    ctypes.memset(ctypes.addressof(target_name), 0, struct_size)
                    
                    ctypes.memmove(ctypes.addressof(target_name), ctypes.byref(ctypes.c_uint32(DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME)), 4)
                    ctypes.memmove(ctypes.addressof(target_name) + 4, ctypes.byref(ctypes.c_uint32(struct_size)), 4)
                    ctypes.memmove(ctypes.addressof(target_name) + 8, ctypes.byref(paths[i].targetInfo.adapterId), 8)
                    ctypes.memmove(ctypes.addressof(target_name) + 16, ctypes.byref(paths[i].targetInfo.id), 4)

                    if ctypes.windll.user32.DisplayConfigGetDeviceInfo(ctypes.byref(target_name)) == 0:
                        name = target_name.monitorFriendlyDeviceName
                        if name:
                            names.append(name)
        except:
            pass
        return names

class MONITORINFOEX(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32)
    ]

class RAMPS(ctypes.Structure):
    _fields_ = [("Red", ctypes.c_ushort * 256),
                ("Green", ctypes.c_ushort * 256),
                ("Blue", ctypes.c_ushort * 256)]

MONITORENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.c_bool, 
    wintypes.HMONITOR, 
    wintypes.HDC, 
    ctypes.POINTER(wintypes.RECT), 
    wintypes.LPARAM  
)

user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), MONITORENUMPROC, wintypes.LPARAM]
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFOEX)]
gdi32.CreateDCW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p]
gdi32.CreateDCW.restype = wintypes.HDC
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.SetDeviceGammaRamp.argtypes = [wintypes.HDC, ctypes.POINTER(RAMPS)]

class GammaNightMode:
    def __init__(self):
        self.enabled = False
        self.strength = 0
        self._monitor_thread = None
        self._running = False
        self._lock = threading.Lock()
        
    def _build_ramp(self, strength: int) -> RAMPS:
        ramp = RAMPS()
        factor = strength / 100.0
        red_factor = 1.0
        green_factor = 1.0 - (factor * 0.4) 
        blue_factor = 1.0 - (factor * 0.9)  
        
        for i in range(256):
            val = i * 256
            if val > 65535: val = 65535
            ramp.Red[i] = int(val * red_factor)
            ramp.Green[i] = int(val * green_factor)
            ramp.Blue[i] = int(val * blue_factor)
        return ramp

    def _apply_to_monitor(self, hMonitor, hdc, lprcMonitor, dwData):
        ramp_ptr = ctypes.cast(dwData, ctypes.POINTER(RAMPS))
        mi = MONITORINFOEX()
        mi.cbSize = ctypes.sizeof(MONITORINFOEX)
        if user32.GetMonitorInfoW(hMonitor, ctypes.byref(mi)):
            dc = gdi32.CreateDCW(None, mi.szDevice, None, None)
            if dc:
                gdi32.SetDeviceGammaRamp(dc, ramp_ptr)
                gdi32.DeleteDC(dc)
        return True

    def apply_gamma(self, strength: int):
        with self._lock:
            strength = max(0, min(100, strength))
            self.strength = strength
            current_ramp = self._build_ramp(strength if self.enabled else 0)
            callback = MONITORENUMPROC(self._apply_to_monitor)
            lparam = wintypes.LPARAM(ctypes.addressof(current_ramp))
            user32.EnumDisplayMonitors(None, None, callback, lparam)

    def enable(self, strength: int):
        self.enabled = True
        self.apply_gamma(strength)
        self._start_monitor()

    def disable(self):
        self.enabled = False
        self.apply_gamma(0)
        self._stop_monitor()

    def toggle(self, strength: int):
        if self.enabled:
            self.disable()
        else:
            self.enable(strength)

    def _monitor_loop(self):
        while self._running:
            time.sleep(2)
            if self.enabled:
                self.apply_gamma(self.strength)

    def _start_monitor(self):
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._running = True
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

    def _stop_monitor(self):
        self._running = False

# ==========================================================
# APP UI
# ==========================================================

class BrightnessApp(ctk.CTk):

    # --- STYLE CONSTANTS ---
    COLOR_BG = "#0F0F0F"           # Deepest Black (OLED style)
    COLOR_CARD_DAY = "#1E1E1E"     # Dark Grey for Brightness
    COLOR_CARD_NIGHT = "#151828"   # Midnight Blue for Night Mode
    
    COLOR_TEXT_MAIN = "#FFFFFF"
    COLOR_TEXT_DIM = "#A0A0A0"
    
    COLOR_SLIDER_DAY = "#E0E0E0"   # White/Silver Glow
    COLOR_SLIDER_NIGHT = "#FFB347" # Amber/Star Glow
    COLOR_KNOB = "#C0C0C0"         # Metallic Knob
    
    FONT_HEADER = ("Segoe UI", 18, "bold")
    FONT_LABEL = ("Segoe UI", 12)

    def __init__(self):
        super().__init__()

        # Init controllers
        self.monitors = sbc.list_monitors()
        self.sliders = []
        self.night = GammaNightMode()
        self.real_names = MonitorNameFetcher().get_real_names()

        # Thread-Safe Brightness
        self._brightness_requests = {}
        self._brightness_lock = threading.Lock()
        self._app_running = True
        threading.Thread(target=self._brightness_worker, daemon=True).start()

        # Window Setup
        self.title("Display Control") 
        
        myappid = 'wbs.lightproject.displaycontrol.1.0'
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)

        # Icon nur für Fensterleiste/Taskleiste laden
        self.icon_path = resource_path("logo.ico")
        
        if os.path.exists(self.icon_path):
            try:
                self.iconbitmap(self.icon_path)
            except:
                pass
        
        # Geometry calc
        # Etwas kompakter, da Header entfernt wurde
        base_height = 280 
        monitor_height = len(self.monitors) * 70
        self.geometry(f"420x{base_height + monitor_height}")
        self.configure(fg_color=self.COLOR_BG)

        self.build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _brightness_worker(self):
        while self._app_running:
            task = None
            with self._brightness_lock:
                if self._brightness_requests:
                    display_idx, value = self._brightness_requests.popitem()
                    task = (display_idx, value)
            
            if task:
                idx, val = task
                try:
                    sbc.set_brightness(val, display=idx)
                except Exception as e:
                    print(f"Error Mon {idx}: {e}")
                time.sleep(0.05) 
            else:
                time.sleep(0.1)

    def get_display_name(self, index, fallback_name):
        if index < len(self.real_names):
            return self.real_names[index]
        return fallback_name

    def build_ui(self):
        # Header (Logo/Titel) komplett entfernt für cleanen Look.
        # Wir fügen oben nur etwas Platz ein ("Spacer").
        
        spacer = ctk.CTkFrame(self, fg_color="transparent", height=15)
        spacer.pack()

        # ==================================================
        # 1. BRIGHTNESS SECTION
        # ==================================================
        
        # Container Card
        day_frame = ctk.CTkFrame(self, fg_color=self.COLOR_CARD_DAY, corner_radius=15)
        day_frame.pack(fill="x", padx=20, pady=5)

        # Title Row in Card
        title_row = ctk.CTkFrame(day_frame, fg_color="transparent")
        title_row.pack(fill="x", padx=15, pady=(15, 5))
        ctk.CTkLabel(title_row, text="☀  Brightness", font=self.FONT_HEADER, text_color=self.COLOR_TEXT_MAIN).pack(side="left")

        # Monitor Sliders
        for i, monitor in enumerate(self.monitors):
            row = ctk.CTkFrame(day_frame, fg_color="transparent")
            row.pack(fill="x", padx=15, pady=5)

            sbc_name = monitor if isinstance(monitor, str) else f"Display {i+1}"
            final_name = self.get_display_name(i, sbc_name)
            if len(final_name) > 20: final_name = final_name[:18] + "..."
            
            # Monitor Label
            ctk.CTkLabel(row, text=final_name, font=self.FONT_LABEL, text_color=self.COLOR_TEXT_DIM, anchor="w").pack(fill="x")

            # Slider matching the logo's left side (Clean, Silver/White Light)
            slider = ctk.CTkSlider(
                day_frame, 
                from_=0, to=100, 
                height=18,             # Etwas dickerer Track
                progress_color=self.COLOR_SLIDER_DAY, 
                fg_color="#333333",    # Dark track background
                button_color=self.COLOR_KNOB,
                button_hover_color="#FFFFFF",
                command=lambda v, idx=i: self.queue_brightness(idx, v)
            )
            slider.pack(fill="x", padx=15, pady=(0, 15)) # Slider unter dem Label

            # Init Value
            try:
                current_val = sbc.get_brightness(display=i)
                val = current_val[0] if isinstance(current_val, list) else current_val
                slider.set(val)
            except:
                slider.set(50)
            self.sliders.append(slider)

        # ==================================================
        # 2. NIGHT MODE SECTION
        # ==================================================

        # Container Card (Midnight Blue background)
        night_frame = ctk.CTkFrame(self, fg_color=self.COLOR_CARD_NIGHT, corner_radius=15, border_width=1, border_color="#2A2F45")
        night_frame.pack(fill="x", padx=20, pady=15)

        # Title Row
        night_title = ctk.CTkFrame(night_frame, fg_color="transparent")
        night_title.pack(fill="x", padx=15, pady=(15, 5))
        ctk.CTkLabel(night_title, text="☾  Night Shift", font=self.FONT_HEADER, text_color="#E0E0FF").pack(side="left")

        # Intensity Slider
        self.strength_slider = ctk.CTkSlider(
            night_frame, 
            from_=0, to=100, number_of_steps=100,
            height=18,
            progress_color=self.COLOR_SLIDER_NIGHT,
            fg_color="#0F111A", # Very dark blue track
            button_color=self.COLOR_KNOB,
            button_hover_color="#FFFFFF",
            command=self.update_strength
        )
        self.strength_slider.pack(fill="x", padx=15, pady=(10, 15))
        self.strength_slider.set(70)

        # Toggle Button
        self.night_button = ctk.CTkButton(
            night_frame, 
            text="Enable Night Mode", 
            font=("Segoe UI", 12, "bold"),
            fg_color="#2A2F45",     # Inactive dark blue
            hover_color="#3A4160",
            text_color="#FFFFFF",
            height=35,
            corner_radius=8,
            command=self.toggle_night
        )
        self.night_button.pack(padx=15, pady=(0, 20), fill="x")

    def queue_brightness(self, display_idx, value):
        with self._brightness_lock:
            self._brightness_requests[display_idx] = int(value)

    def update_strength(self, value):
        if self.night.enabled:
            self.night.apply_gamma(int(value))

    def toggle_night(self):
        strength = int(self.strength_slider.get())
        self.night.toggle(strength)
        
        if self.night.enabled:
            # Active State: Glowing Orange Button (matches slider)
            self.night_button.configure(
                text="Night Mode Active", 
                fg_color=self.COLOR_SLIDER_NIGHT, 
                text_color="#000000", # Black text on bright orange
                hover_color="#E59E35"
            )
            self.night.apply_gamma(strength) 
        else:
            # Inactive State
            self.night_button.configure(
                text="Enable Night Mode", 
                fg_color="#2A2F45", 
                text_color="#FFFFFF",
                hover_color="#3A4160"
            )

    def on_close(self):
        self._app_running = False
        self.night.disable()
        self.destroy()

if __name__ == "__main__":
    app = BrightnessApp()
    app.mainloop()