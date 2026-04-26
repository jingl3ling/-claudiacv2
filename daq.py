"""
Claudiac DAQ - 实时 ECG 显示 + 滚动窗口 + 一键保存
=====================================================
硬件: Arduino Uno + EXG Pill, COM9, 256 Hz, 10-bit ADC

用法:
    pip install pyserial numpy matplotlib scipy
    python daq.py
    
    - 实时滚动显示 ECG 波形
    - 按 's' 键: 保存最近 30 秒为 data/live_ecg.npz
    - 关闭窗口退出
"""
import serial
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from collections import deque
import time
import os

# ============ 配置 ============
PORT = 'COM9'           # Arduino Uno
BAUD = 115200
FS = 256                # 采样率,要和固件里的一致
DISPLAY_SEC = 6         # 屏幕显示窗口长度
SAVE_SEC = 30           # 保存时的窗口长度
ADC_MAX = 1023          # Arduino Uno 是 10-bit ADC
# =================================

DISPLAY_SIZE = FS * DISPLAY_SEC
SAVE_SIZE = FS * SAVE_SEC

# 滚动缓冲区
display_buffer = deque([ADC_MAX // 2] * DISPLAY_SIZE, maxlen=DISPLAY_SIZE)
save_buffer = deque(maxlen=SAVE_SIZE)

# 串口
print(f"打开串口 {PORT} @ {BAUD}...")
ser = serial.Serial(PORT, BAUD, timeout=1)
time.sleep(2)
ser.reset_input_buffer()
print("串口就绪。按 's' 保存最近 30 秒,关闭窗口退出。")

last_save_msg = ""
last_save_time = 0

# matplotlib 设置
plt.rcParams['toolbar'] = 'None'
fig, ax = plt.subplots(figsize=(12, 4))
fig.canvas.manager.set_window_title('Claudiac ECG - Live')

t_axis = np.linspace(-DISPLAY_SEC, 0, DISPLAY_SIZE)
line, = ax.plot(t_axis, list(display_buffer), color='#d63031', linewidth=1.2)
ax.set_xlabel('Time (s)')
ax.set_ylabel('ADC value')
ax.set_xlim(-DISPLAY_SEC, 0)
ax.set_ylim(0, ADC_MAX)
ax.set_title('ECG (raw ADC) — press "s" to save 30s window', fontsize=11)
ax.grid(alpha=0.3)
status_text = ax.text(0.02, 0.95, '', transform=ax.transAxes,
                      fontsize=10, verticalalignment='top',
                      bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))


def read_serial_into_buffers():
    """把串口缓冲区里所有可读的样本都吸进来"""
    n = 0
    while ser.in_waiting > 0:
        line_bytes = ser.readline()
        try:
            s = line_bytes.decode('utf-8', errors='ignore').strip()
            if s.isdigit():
                v = int(s)
                if 0 <= v <= ADC_MAX:
                    display_buffer.append(v)
                    save_buffer.append(v)
                    n += 1
        except Exception:
            pass
        if n > FS:
            break
    return n


def save_window():
    """保存最近 SAVE_SEC 秒为 npz"""
    global last_save_msg, last_save_time
    if len(save_buffer) < SAVE_SIZE:
        last_save_msg = f"⚠ 数据不足,只有 {len(save_buffer)/FS:.1f}s,需要 {SAVE_SEC}s"
        last_save_time = time.time()
        return
    
    arr = np.array(list(save_buffer), dtype=np.float32)
    arr_norm = (arr - arr.mean()) / arr.std()
    
    os.makedirs('data', exist_ok=True)
    out_path = 'data/live_ecg.npz'
    np.savez(out_path, ecg=arr_norm, ecg_raw=arr, fs=FS)
    
    last_save_msg = f"✓ 已保存 {SAVE_SEC}s @ {FS}Hz → {out_path}"
    last_save_time = time.time()
    print(last_save_msg)


def on_key(event):
    if event.key == 's':
        save_window()


fig.canvas.mpl_connect('key_press_event', on_key)


def update(frame):
    n_new = read_serial_into_buffers()
    line.set_ydata(list(display_buffer))
    
    buf_sec = len(save_buffer) / FS
    status = f"buffer: {buf_sec:.1f}/{SAVE_SEC}s   new samples: {n_new}"
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