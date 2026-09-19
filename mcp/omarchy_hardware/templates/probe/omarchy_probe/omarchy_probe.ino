// omarchy-hardware bench probe: reports what is on the I2C bus and drives nothing.
//
// It scans every 7-bit address on the default Wire bus, then reads the chip-ID
// register of devices that have one (the ID_READS table, kept in step with
// omarchy_hardware/peripherals.toml by a test). Reads only: a register pointer
// is set with a repeated start, and no data byte is ever written.
//
// Output, 115200 baud, one JSON object per line:
//   {"fw":"omarchy_probe","build":"<date> <time>"}
//   {"probe":"done","bus":"Wire","i2c":[{"a":"0x76","id":{"0xd0":"0x60"}}]}
//   {"selftest":true,"i2c_count":1}
// The report repeats every 5 seconds, so a monitor opened late still sees it.
//
// Works with any core that provides Wire: AVR, ESP32, RP2040 (arduino-pico) and
// STM32 (stm32duino). The bus uses the core's default SDA/SCL pins; see
// board_profile for which pins those are.

#include <Wire.h>

struct IdRead {
  uint8_t address;
  uint8_t reg;
};

const IdRead ID_READS[] = {
  {0x18, 0x0f}, {0x19, 0x0f}, {0x1d, 0x00}, {0x1e, 0x0a},
  {0x29, 0x92}, {0x29, 0xc0}, {0x53, 0x00}, {0x57, 0xff},
  {0x5a, 0x20}, {0x5b, 0x20}, {0x68, 0x00}, {0x68, 0x75},
  {0x69, 0x00}, {0x69, 0x75}, {0x76, 0xd0}, {0x77, 0xd0},
};
const size_t ID_READ_COUNT = sizeof(ID_READS) / sizeof(ID_READS[0]);

void printHex(uint8_t value) {
  Serial.print("\"0x");
  if (value < 0x10) Serial.print('0');
  Serial.print(value, HEX);
  Serial.print('"');
}

// Returns -1 when the device does not answer the read.
int readRegister(uint8_t address, uint8_t reg) {
  Wire.beginTransmission(address);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return -1;
  if (Wire.requestFrom(address, (uint8_t)1) != 1) return -1;
  return Wire.read();
}

void report() {
  int found = 0;
  Serial.print("{\"probe\":\"done\",\"bus\":\"Wire\",\"i2c\":[");
  for (uint8_t address = 0x08; address <= 0x77; address++) {
    Wire.beginTransmission(address);
    if (Wire.endTransmission() != 0) continue;

    if (found++) Serial.print(',');
    Serial.print("{\"a\":");
    printHex(address);
    Serial.print(",\"id\":{");
    int reads = 0;
    for (size_t i = 0; i < ID_READ_COUNT; i++) {
      if (ID_READS[i].address != address) continue;
      int value = readRegister(address, ID_READS[i].reg);
      if (value < 0) continue;
      if (reads++) Serial.print(',');
      printHex(ID_READS[i].reg);
      Serial.print(':');
      printHex((uint8_t)value);
    }
    Serial.print("}}");
  }
  Serial.println("]}");
  Serial.print("{\"selftest\":true,\"i2c_count\":");
  Serial.print(found);
  Serial.println('}');
}

void setup() {
  Serial.begin(115200);
  unsigned long start = millis();
  while (!Serial && millis() - start < 3000) {
    // Native-USB boards: wait briefly for the host to open the port.
  }
  Serial.print("{\"fw\":\"omarchy_probe\",\"build\":\"");
  Serial.print(__DATE__ " " __TIME__);
  Serial.println("\"}");
  Wire.begin();
}

void loop() {
  report();
  delay(5000);
}
