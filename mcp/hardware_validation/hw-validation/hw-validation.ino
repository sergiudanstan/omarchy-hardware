// omarchy-hardware validation sketch: announces itself, then echoes each line.
void setup() {
  Serial.begin(115200);
  Serial.println("HWVAL READY");
}

void loop() {
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line == "PING") {
      Serial.println("PONG");
    } else {
      Serial.print("ECHO:");
      Serial.println(line);
    }
  }
}
