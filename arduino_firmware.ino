/*
  Arduino Firmware pre 8mm Film Scanner
  Ovládanie krokového motora (napr. NEMA 17 s driverom A4988 / DRV8825 / TMC2208)
  
  Zapojenie pinov:
    STEP_PIN  -> Arduino Pin 3 (Step / Impulz kroku)
    DIR_PIN   -> Arduino Pin 2 (Smer otáčania)
    ENABLE_PIN-> Arduino Pin 4 (Voliteľný Enable pin, LOW = motor aktívny)
    TRIGGER_PIN -> Arduino Pin 5 (Voliteľný vstup z optozávory/mikrospínača políčka)

  Sériová komunikácia: 115200 baud
  Príkazy z PC (cez USB):
    "STEP"         -> Spraví definovaný počet krokov na 1 políčko (predvolené 200)
    "STEP:N"       -> Spraví N krokov (napr. STEP:100 alebo STEP:-100 pre spätný chod)
    "SET_STEPS:N"  -> Nastaví predvolený počet krokov na 1 políčko
    "SET_SPEED:N"  -> Nastaví pauzu medzi krokmi v mikrosekundách (rýchlosť, predvolené 1000 us)
    "PING"         -> Odpovie "PONG" (pre overenie pripojenia v GUI)
*/

const int DIR_PIN = 2;
const int STEP_PIN = 3;
const int ENABLE_PIN = 4;
const int TRIGGER_PIN = 5;

long stepsPerFrame = 200;      // Počet krokov na jedno políčko filmu (nastaviteľné podľa prevodu)
int stepDelayMicros = 1000;    // Rýchlosť motora (menšie číslo = rýchlejší chod)

void setup() {
  Serial.begin(115200);
  while (!Serial) { ; } // Čakanie na otvorenie sériového portu

  pinMode(DIR_PIN, OUTPUT);
  pinMode(STEP_PIN, OUTPUT);
  pinMode(ENABLE_PIN, OUTPUT);
  pinMode(TRIGGER_PIN, INPUT_PULLUP);

  // Zapnutie drivera (pre A4988/TMC2208: LOW = ENABLED)
  digitalWrite(ENABLE_PIN, LOW);

  Serial.println("READY:8MM_SCANNER_CONTROLLER_V1");
}

void stepMotor(long steps) {
  if (steps == 0) return;

  // Určenie smeru
  if (steps > 0) {
    digitalWrite(DIR_PIN, HIGH);
  } else {
    digitalWrite(DIR_PIN, LOW);
    steps = -steps;
  }

  // Generovanie impulzov
  for (long i = 0; i < steps; i++) {
    digitalWrite(STEP_PIN, HIGH);
    delayMicroseconds(stepDelayMicros);
    digitalWrite(STEP_PIN, LOW);
    delayMicroseconds(stepDelayMicros);
  }

  // Odpoveď do PC, že posun je dokončený
  Serial.println("STEP_DONE");
}

void loop() {
  if (Serial.available() > 0) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();

    if (cmd == "STEP") {
      stepMotor(stepsPerFrame);
    } 
    else if (cmd.startsWith("STEP:")) {
      long customSteps = cmd.substring(5).toInt();
      stepMotor(customSteps);
    } 
    else if (cmd.startsWith("SET_STEPS:")) {
      stepsPerFrame = cmd.substring(10).toInt();
      Serial.print("ACK_STEPS:");
      Serial.println(stepsPerFrame);
    } 
    else if (cmd.startsWith("SET_SPEED:")) {
      stepDelayMicros = cmd.substring(10).toInt();
      Serial.print("ACK_SPEED:");
      Serial.println(stepDelayMicros);
    } 
    else if (cmd == "PING") {
      Serial.println("PONG");
    }
  }
}
