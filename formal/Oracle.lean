import OmarchyFormal

/-!
Executable form of the Lean models, for `tests/differential.py`: one request per line on
stdin, one answer per line on stdout. The functions called are the same definitions the
theorems are stated about.
-/

open Omarchy

def bit (b : Bool) : String := if b then "1" else "0"

def parseArea : String → Option Modbus.Area
  | "LB" => some .LB
  | "LW" => some .LW
  | "RW" => some .RW
  | _ => none

def parseRange (s : String) : Option Modbus.Range := do
  match s.splitOn ":" with
  | [a, st, c] => pure ⟨← parseArea a, ← st.toNat?, ← c.toNat?⟩
  | _ => none

def parseEvent (s : String) : Option (Int × Nat) := do
  match s.splitOn ":" with
  | [t, c] => pure (← t.toInt?, ← c.toNat?)
  | _ => none

def answer (line : String) : String :=
  match line.splitOn "\t" with
  | ["matches", f, t] => bit (Mqtt.topicMatchesStr f t)
  | ["covers", a, r] => bit (Mqtt.filterCoversStr a r)
  | ["enc", n] =>
    match n.toNat? with
    | some n => " ".intercalate ((Mqtt.encodeLength n).map toString)
    | none => "error"
  | ["dec", bs] =>
    let bytes := (bs.splitOn " ").filterMap String.toNat?
    match Mqtt.decodeLength bytes with
    | some (n, rest) => s!"{n} {rest.length}"
    | none => "none"
  | ["pid", n] => toString (Mqtt.nextPacketId (n.toNat?.getD 0))
  | ["valid", r] => (parseRange r).map (bit ·.valid) |>.getD "error"
  | ["modbus", allow, a, s, c] =>
    match (allow.splitOn ",").mapM parseRange, parseArea a, s.toNat?, c.toNat? with
    | some rs, some a, some s, some c =>
      let (fn, addr) := Modbus.modbusAddress a s
      s!"{bit (Modbus.authorised rs a s c)} {fn} {addr}"
    | _, _, _, _ => "error"
  | ["budget", limit, w, evs] =>
    match limit.toNat?, w.toInt?, (evs.splitOn ",").mapM parseEvent with
    | some limit, some w, some reqs =>
      -- Replay one charge at a time so every decision is reported, not just the log.
      let step := fun (acc : List Budget.Event × String) (r : Int × Nat) =>
        match Budget.charge limit w acc.1 r.1 r.2 with
        | some st => (st, acc.2 ++ "A")
        | none => (acc.1, acc.2 ++ "R")
      (reqs.foldl step ([], "")).2
    | _, _, _ => "error"
  | _ => "error"

partial def loop (stdin : IO.FS.Stream) (stdout : IO.FS.Stream) : IO Unit := do
  let line ← stdin.getLine
  if line.isEmpty then return
  stdout.putStrLn (answer (String.ofList (line.toList.filter (· != '\n'))))
  loop stdin stdout

def main : IO Unit := do
  loop (← IO.getStdin) (← IO.getStdout)
