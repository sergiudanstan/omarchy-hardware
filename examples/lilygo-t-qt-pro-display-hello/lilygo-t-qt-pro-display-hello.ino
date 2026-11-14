// LilyGO T-QT Pro (ESP32-S3), FQBN:
// esp32:esp32:esp32s3:FlashSize=4M,PSRAM=enabled,CDCOnBoot=cdc
// GFX Library for Arduino 1.6.8
#include <Arduino_GFX_Library.h>

#define TFT_MOSI 2
#define TFT_SCLK 3
#define TFT_CS 5
#define TFT_DC 6
#define TFT_RST 1
#define TFT_BL 10

Arduino_DataBus *bus = new Arduino_ESP32SPI(TFT_DC, TFT_CS, TFT_SCLK, TFT_MOSI, GFX_NOT_DEFINED);
Arduino_GFX *gfx = new Arduino_GC9107(bus, TFT_RST, 0, true);

uint32_t lastFw = 0;

void printFw() {
  Serial.printf("{\"fw\":\"lilygo-t-qt-pro-display-hello\",\"build\":\"%s %s\"}\n", __DATE__, __TIME__);
}

void setup() {
  Serial.begin(115200);
  printFw();

  pinMode(TFT_BL, OUTPUT);
  digitalWrite(TFT_BL, LOW);  // Backlight is active low.

  if (!gfx->begin()) {
    Serial.println("{\"selftest\":false,\"reason\":\"display init failed\"}");
    return;
  }

  gfx->fillScreen(0x0000);
  gfx->drawRect(0, 0, 128, 128, 0x07E0);
  gfx->setTextColor(0xFFFF);
  gfx->setTextSize(1);
  gfx->setCursor(16, 48);
  gfx->print("Omarchy");
  gfx->setCursor(16, 64);
  gfx->print("Hardware");
  gfx->fillRect(8, 100, 37, 12, 0xF800);
  gfx->fillRect(45, 100, 37, 12, 0x07E0);
  gfx->fillRect(82, 100, 38, 12, 0x001F);

  Serial.println("{\"selftest\":true,\"display\":\"GC9107 128x128\"}");
  lastFw = millis();
}

void loop() {
  if (millis() - lastFw >= 5000) {
    printFw();
    lastFw = millis();
  }
}
