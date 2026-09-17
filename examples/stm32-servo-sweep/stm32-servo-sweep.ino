// Continuous servo sweep with a hard angle limit.
//
// Board: STM32 Nucleo-F411RE (STMicroelectronics:stm32:Nucleo_64:pnum=NUCLEO_F411RE)
// Wiring: servo signal to D3, servo GND to board GND, servo power to 5V or an
// external 5 V supply for larger servos.
// Serial: 115200 baud on the ST-LINK virtual COM port, one line per angle.
//
// Physically checked 2026-09-17: 0.5 s per 0 -> 50 -> 0 sweep, angles 0..50.
#include <Servo.h>

const int SERVO_PIN = 3;
const int MAX_ANGLE = 50;     // mechanical limit of the tested servo; every write is clamped to it
const int STEP = 5;
const int STEP_DELAY_MS = 25; // 20 ms is the servo frame, so shorter delays do not speed it up

Servo servo;
unsigned long cycle = 0;

void moveTo(int angle) {
  angle = constrain(angle, 0, MAX_ANGLE);
  servo.write(angle);
  Serial.print("angle ");
  Serial.println(angle);
}

void setup() {
  Serial.begin(115200);
  // attach(pin, value) sets the first pulse, so the servo never goes to the 90 degree default.
  servo.attach(SERVO_PIN, 0);
  Serial.print("START continuous sweep D3 max ");
  Serial.println(MAX_ANGLE);
}

void loop() {
  Serial.print("sweep ");
  Serial.println(++cycle);
  for (int a = 0; a <= MAX_ANGLE; a += STEP) { moveTo(a); delay(STEP_DELAY_MS); }
  for (int a = MAX_ANGLE - STEP; a > 0; a -= STEP) { moveTo(a); delay(STEP_DELAY_MS); }
}
