"""
Claudiac DAQ - 实时 ECG 显示 + 滚动窗口 + 一键保存
=====================================================
硬件: Arduino Uno + EXG Pill, COM9
固件: Upside Down Labs 官方 ECGFilter (0.5–44.5 Hz Butterworth)
采样率: 125 Hz (与固件滤波器系数耦合,不能改)

用法:
    pip install pyserial numpy matplotlib scipy
    python daq.py

    - 实时滚动显示 ECG 波形
    - 按 's' 键: 保存最近 30 秒为 data/live_ecg.npz
    - 按 'n' 键: 切换 50Hz 工频陷波 (默认开)
    - 关闭窗口退出
"""
import serial
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from collections import deque
from scipy.signal import iirnotch, lfilter, lfilter_zi
import time
import os

# ============ 配置 ============
PORT = 'COM9'
BAUD = 115200
FS = 125                # 必须和固件一致
DISPLAY_SEC = 6
SAVE_SEC = 30
NOTCH_FREQ = 50         # 中国电网 50 Hz; 北美改 60
NOTCH_Q = 30            # 陷波 Q 值,越高越窄
# =================================

DISPLAY_SIZE = FS * DISPLAY_SEC
SAVE_SIZE = FS * SAVE_SEC

display_buffer = deque([0.0] * DISPLAY_SIZE, maxlen=DISPLAY_SIZE)
save_buffer = deque(maxlen=SAVE_SIZE)

# 50 Hz 陷波滤波器(实时流式,带状态)
b_notch, a_notch = iirnotch(NOTCH_FREQ, NOTCH_Q, FS)
notch_zi = lfilter_zi(b_notch, a_notch) * 0.0  # 初始状态
notch_enabled = True

# 串口
print(f"打开串口 {PORT} @ {BAUD}...")
try:
    ser = serial.Serial(PORT, BAUD, timeout=1)
except serial.SerialException as e:
    print(f"\n❌ 无法打开 {PORT}: {e}")
    print("   关掉 Arduino IDE 的 Serial Monitor / Plotter 再试。")
    raise SystemExit(1)
time.sleep(2)
ser.reset_input_buffer()
print(f"串口就绪 @ {FS}Hz。按 's' 保存 30s,'n' 切换陷波,关闭窗口退出。")

last_save_msg = ""
last_save_time = 0

# matplotlib
plt.rcParams['toolbar'] = 'None'
fig, ax = plt.subplots(figsize=(12, 4))
fig.canvas.manager.set_window_title('Claudiac ECG - Live')

t_axis = np.linspace(-DISPLAY_SEC, 0, DISPLAY_SIZE)
line, = ax.plot(t_axis, list(display_buffer), color='#d63031', linewidth=1.2)
ax.set_xlabel('Time (s)')
ax.set_ylabel('Filtered amplitude')
ax.set_xlim(-DISPLAY_SEC, 0)
ax.set_ylim(-300, 300)   # 浮点滤波信号,初始范围;后面自动调整
ax.set_title(f'ECG (filtered @ {FS}Hz) — press "s" to save, "n" to toggle notch',
             fontsize=11)
ax.grid(alpha=0.3)
status_text = ax.text(0.02, 0.95, '', transform=ax.transAxes,
                      fontsize=10, verticalalignment='top',
                      bbox=dict(boxstyle='round', facecolor='white', alpha=0.85))


def read_serial_into_buffers():
    """读取串口浮点流,可选叠加 50Hz notch,推进缓冲区"""
    global notch_zi
    n = 0
    new_samples = []
    while ser.in_waiting > 0:
        line_bytes = ser.readline()
        try:
            s = line_bytes.decode('utf-8', errors='ignore').strip()
            v = float(s)
            new_samples.append(v)
            n += 1
        except (ValueError, UnicodeDecodeError):
            pass
        if n > FS:
            break

    if not new_samples:
        return 0

    arr = np.array(new_samples, dtype=np.float32)
    if notch_enabled:
        arr, notch_zi = lfilter(b_notch, a_notch, arr, zi=notch_zi)

    for v in arr:
        display_buffer.append(float(v))
        save_buffer.append(float(v))
    return n


def auto_scale_y():
    """根据当前显示窗口自动调整 y 轴"""
    arr = np.array(display_buffer)
    if arr.std() > 1e-6:
        center = arr.mean()
        span = max(np.abs(arr - center).max() * 1.2, 50)
        ax.set_ylim(center - span, center + span)


def save_window():
    global last_save_msg, last_save_time
    if len(save_buffer) < SAVE_SIZE:
        last_save_msg = f"⚠ 数据不足: {len(save_buffer)/FS:.1f}/{SAVE_SEC}s"
        last_save_time = time.time()
        return

    arr = np.array(list(save_buffer), dtype=np.float32)
    arr_norm = (arr - arr.mean()) / (arr.std() + 1e-9)

    os.makedirs('data', exist_ok=True)
    out_path = 'data/live_ecg.npz'
    np.savez(out_path, ecg=arr_norm, ecg_filtered=arr, fs=FS)

    last_save_msg = f"✓ 已保存 {SAVE_SEC}s @ {FS}Hz → {out_path}"
    last_save_time = time.time()
    print(last_save_msg)


def on_key(event):
    global notch_enabled
    if event.key == 's':
        save_window()
    elif event.key == 'n':
        notch_enabled = not notch_enabled
        print(f"50Hz notch: {'ON' if notch_enabled else 'OFF'}")


fig.canvas.mpl_connect('key_press_event', on_key)

frame_count = 0
def update(frame):
    global frame_count
    frame_count += 1
    n_new = read_serial_into_buffers()
    line.set_ydata(list(display_buffer))

    # 每 10 帧自动缩放一次 y 轴
    if frame_count % 10 == 0:
        auto_scale_y()

    buf_sec = len(save_buffer) / FS
    notch_str = "ON" if notch_enabled else "OFF"
    status = (f"buffer: {buf_sec:.1f}/{SAVE_SEC}s   "
              f"new: {n_new}   notch: {notch_str}")
    if last_save_msg and (time.time() - last_save_time) < 4:
        status += f"\n{last_save_msg}"
    status_text.set_text(status)

    return line, status_text


ani = FuncAnimation(fig, update, interval=50, blit=False, cache_frame_data=False)

try:
    plt.show()
finally:
    ser.close()
    print("串口已关闭。")