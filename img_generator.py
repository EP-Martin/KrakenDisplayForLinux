#!/usr/bin/env python3
import threading
import time
import subprocess
import os
import pynvml
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageDraw, ImageFont


# =========================
# CONFIG
# =========================
W, H = 320, 320
PADDING = 20
SAFE_W = W - (PADDING * 2)

BAR_WIDTH = SAFE_W
BAR_HEIGHT = 22
BAR_RADIUS = BAR_HEIGHT // 2
BAR_X = PADDING

CPU_TEXT_Y = PADDING + 40
BAR_CPU_Y  = PADDING + 110
BAR_GPU_Y  = PADDING + 145
GPU_TEXT_Y = PADDING + 215

BG_COLOR   = "#000000FF"
BAR_BG     = "#BEC0C4FF"
CPU_FG     = "#4900D0FF"
GPU_FG     = "#A033BEFF"

TEXT_MAIN  = "#FFFFFFFF"
TEXT_SUB   = "#FFFFFFFF"


OUTPUT_PNG = "/home/erikm/git/TempBarsGen/img/logo.png"
LIQUIDCTL_CMD = ['liquidctl', '--match', 'Kraken', 'set', 'lcd', 'screen', 'static', OUTPUT_PNG]

# Interpolazione
REFRESH_S = 1.0          # ogni quanto aggiorni
SMOOTHING = 0.35         # 0..1 (più alto = più veloce verso il reale)
MAX_STEP = 4             # max gradi per step (limita “salti”)

# Font (Gotham SSm)
FONT_PATH = "/home/your_username/.local/share/fonts/g/gothamnarrssm_black.otf"
font_label = ImageFont.truetype(FONT_PATH, 36)
font_temp_value = ImageFont.truetype(FONT_PATH, 95)
font_degree = ImageFont.truetype(FONT_PATH, 32)

# =========================
# FUNZIONI GRAFICHE
# =========================
def clamp(x, a, b):
    return max(a, min(b, x))

def temp_to_width(temp, max_width):
    temp = clamp(temp, 0, 100)
    return int((temp / 100) * max_width)

def draw_bar(draw, x, y, width, height, radius, bg_color, fg_color, value_width):
    draw.rounded_rectangle([x, y, x + width, y + height], radius=radius, fill=bg_color)
    if value_width > 0:
        draw.rounded_rectangle([x, y, x + value_width, y + height], radius=radius, fill=fg_color)

def draw_temp_with_degree(draw, right_x, y, temp_int, font_value, font_degree, fill,
                          degree_x_offset=0, degree_y_offset=20):
    value_str = str(int(temp_int))

    bbox_val = draw.textbbox((0, 0), value_str, font=font_value)
    val_w = bbox_val[2] - bbox_val[0]

    x_val = right_x - val_w
    draw.text((x_val, y), value_str, font=font_value, fill=fill)

    # simbolo °
    x_deg = right_x + degree_x_offset
    y_deg = y + degree_y_offset
    draw.text((x_deg, y_deg), "°", font=font_degree, fill=fill)

def render_frame(cpu_temp, gpu_temp):
    img = Image.new("RGBA", (W, H), BG_COLOR)
    draw = ImageDraw.Draw(img)

    cpu_width = temp_to_width(cpu_temp, BAR_WIDTH)
    gpu_width = temp_to_width(gpu_temp, BAR_WIDTH)

    draw_bar(draw, BAR_X, BAR_CPU_Y, BAR_WIDTH, BAR_HEIGHT, BAR_RADIUS, BAR_BG, CPU_FG, cpu_width)
    draw_bar(draw, BAR_X, BAR_GPU_Y, BAR_WIDTH, BAR_HEIGHT, BAR_RADIUS, BAR_BG, GPU_FG, gpu_width)

    TEMP_RIGHT_EDGE = W - PADDING - 50

    # Temperature (ancorate a destra) + simbolo °
    draw_temp_with_degree(draw, TEMP_RIGHT_EDGE, CPU_TEXT_Y - 40, cpu_temp,
                          font_temp_value, font_degree, TEXT_MAIN)

    draw_temp_with_degree(draw, TEMP_RIGHT_EDGE, GPU_TEXT_Y - 60, gpu_temp,
                          font_temp_value, font_degree, TEXT_MAIN)

    # Labels
    draw.text((BAR_X + 10, CPU_TEXT_Y + 15), "CPU", font=font_label, fill=TEXT_SUB)
    draw.text((BAR_X + 10, GPU_TEXT_Y - 45), "GPU", font=font_label, fill=TEXT_SUB)

    img.convert("RGB").save(OUTPUT_PNG, format="PNG", compress_level=1)

# =========================
# FUNZIONI TEMPERATURE (INT)
# =========================

# Initialize NVML once — no per-call driver handshake overhead
pynvml.nvmlInit()
_gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)

def find_hwmon_by_name(name):
    base = "/sys/class/hwmon"
    for entry in os.listdir(base):
        path = os.path.join(base, entry)
        try:
            with open(os.path.join(path, "name")) as f:
                if f.read().strip() == name:
                    return path
        except OSError:
            pass
    return None

_cpu_hwmon = find_hwmon_by_name("k10temp") # or "coretemp" for Intel

def get_cpu_temp_int():
    if _cpu_hwmon is None:
        return None
    try:
        with open(os.path.join(_cpu_hwmon, "temp1_input")) as f:
            return int(f.read().strip()) // 1000
    except Exception:
        return None

def get_gpu_temp_int():
    try:
        return pynvml.nvmlDeviceGetTemperature(_gpu_handle, pynvml.NVML_TEMPERATURE_GPU)
    except pynvml.NVMLError:
        return None

# =========================
# INTERPOLAZIONE
# =========================
def smooth_step(display_val, target_val):
    """
    display_val -> target_val con smoothing + step max.
    Ritorna un INT.
    """
    if target_val is None:
        return display_val

    if display_val is None:
        return int(target_val)

    diff = target_val - display_val

    # avvicinamento morbido
    step = int(round(diff * SMOOTHING))

    # se lo smoothing produce 0 ma siamo lontani, muoviti di 1
    if step == 0 and diff != 0:
        step = 1 if diff > 0 else -1

    # limita i salti
    step = clamp(step, -MAX_STEP, MAX_STEP)

    return int(display_val + step)

# =========================
# IMPOSTAZIONE PRINCIPALE LCD
# =========================

def init_kraken():
    subprocess.run(
        ["liquidctl", "--match", "Kraken", "initialize"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

def set_lcd_orientation():
    subprocess.run(
        ["liquidctl", "--match", "Kraken", "set", "lcd", "screen", "orientation", "270"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

def set_lcd_brightness():
    subprocess.run(
        ["liquidctl", "--match", "Kraken", "set", "lcd", "screen", "brightness", "100"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

_lcd_lock = threading.Lock()

def send_to_lcd():
    with _lcd_lock:
        try:
            subprocess.run(LIQUIDCTL_CMD, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

# =========================
# MAIN LOOP
# =========================
def main():

    time.sleep(2)
    init_kraken()

    set_lcd_orientation()
    set_lcd_brightness()

    cpu_disp = None
    gpu_disp = None

    with ThreadPoolExecutor(max_workers=2) as executor:
        while True:
            fut_cpu = executor.submit(get_cpu_temp_int)
            fut_gpu = executor.submit(get_gpu_temp_int)
            cpu_real = fut_cpu.result()
            gpu_real = fut_gpu.result()

            cpu_disp = smooth_step(cpu_disp, cpu_real)
            gpu_disp = smooth_step(gpu_disp, gpu_real)

            # clamp da 0 a 100 per la barra
            cpu_show = clamp(cpu_disp if cpu_disp is not None else 0, 0, 100)
            gpu_show = clamp(gpu_disp if gpu_disp is not None else 0, 0, 100)

            render_frame(cpu_show, gpu_show)

            # invia al Kraken
            t = threading.Thread(target=send_to_lcd, daemon=True)
            t.start()

            time.sleep(REFRESH_S)


if __name__ == "__main__":
    main()
