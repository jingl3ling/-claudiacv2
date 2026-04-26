#define ECG_PIN A0
#define SAMPLE_RATE 256
#define SAMPLE_INTERVAL_US (1000000 / SAMPLE_RATE)

unsigned long lastSample = 0;

void setup() {
  Serial.begin(115200);
  pinMode(ECG_PIN, INPUT);
}

void loop() {
  unsigned long now = micros();
  if (now - lastSample >= SAMPLE_INTERVAL_US) {
    lastSample = now;
    int raw = analogRead(ECG_PIN);  // Uno 是 10-bit, 0-1023
    Serial.println(raw);
  }
}