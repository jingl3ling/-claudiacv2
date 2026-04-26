/*
 * Claudiac ECG Capture (with EXG Filter)
 * =======================================
 * 板子: Arduino Uno
 * 传感器: EXG Pill (BioAmp)
 * 滤波: Upside Down Labs 官方 Butterworth 0.5-44.5 Hz @ 125 Hz
 *
 * 接线:
 *   EXG Pill VCC -> Arduino 5V
 *   EXG Pill GND -> Arduino GND
 *   EXG Pill OUT -> Arduino A0
 *
 * 输出: 串口 115200,每行一个滤波后的浮点值
 * 采样率: 125 Hz (固定,与滤波器系数耦合)
 */

#define SAMPLE_RATE 125
#define BAUD_RATE 115200
#define INPUT_PIN A0

void setup() {
  Serial.begin(BAUD_RATE);
}

void loop() {
  static unsigned long past = 0;
  unsigned long present = micros();
  unsigned long interval = present - past;
  past = present;

  static long timer = 0;
  timer -= interval;

  if (timer < 0) {
    timer += 1000000 / SAMPLE_RATE;
    float sensor_value = analogRead(INPUT_PIN);
    float signal = ECGFilter(sensor_value);
    Serial.println(signal, 3);   // 3 位小数,够用又省带宽
  }
}

// Band-Pass Butterworth IIR, 0.5–44.5 Hz @ 125 Hz, order 4 (biquads)
float ECGFilter(float input) {
  float output = input;
  {
    static float z1, z2;
    float x = output - 0.70682283 * z1 - 0.15621030 * z2;
    output = 0.28064917 * x + 0.56129834 * z1 + 0.28064917 * z2;
    z2 = z1; z1 = x;
  }
  {
    static float z1, z2;
    float x = output - 0.95028224 * z1 - 0.54073140 * z2;
    output = 1.00000000 * x + 2.00000000 * z1 + 1.00000000 * z2;
    z2 = z1; z1 = x;
  }
  {
    static float z1, z2;
    float x = output - -1.95360385 * z1 - 0.95423412 * z2;
    output = 1.00000000 * x + -2.00000000 * z1 + 1.00000000 * z2;
    z2 = z1; z1 = x;
  }
  {
    static float z1, z2;
    float x = output - -1.98048558 * z1 - 0.98111344 * z2;
    output = 1.00000000 * x + -2.00000000 * z1 + 1.00000000 * z2;
    z2 = z1; z1 = x;
  }
  return output;
}