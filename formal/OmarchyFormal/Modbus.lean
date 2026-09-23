/-!
# Weintek Modbus read authorisation

Models of `config.ModbusRange.covers`, `config._modbus_range`,
`policy.check_weintek_modbus`, `weintek.modbus_address` and the count bound in
`server.weintek_modbus_read`.

The chain from tool call to wire is: `_bounded(count, 1, limit)` → `covers` against an
allowlist entry that `_modbus_range` accepted → `modbus_address` → `struct.pack(">…HH",
address, count)`. The theorems show that every register on the wire is one the allowlist
names, that the packed fields fit 16 bits, and that LW and RW never alias.
-/

namespace Omarchy.Modbus

inductive Area | LB | LW | RW
  deriving DecidableEq, Repr

/-- `MODBUS_AREAS` -/
def areaSize : Area → Nat
  | .LB => 12800
  | .LW => 9999
  | .RW => 55536

/-- `weintek.MAX_MODBUS_BITS` / `MAX_MODBUS_WORDS` -/
def maxCount : Area → Nat
  | .LB => 256
  | _ => 64

structure Range where
  area : Area
  start : Nat
  count : Nat

/-- `ModbusRange.covers` -/
def Range.covers (r : Range) (a : Area) (s c : Nat) : Bool :=
  a == r.area && r.start ≤ s && s + c ≤ r.start + r.count

/-- What `_modbus_range` accepts. `MODBUS_RANGE` allows at most five start digits and four
count digits, then `count >= 1` and `start + count <= MODBUS_AREAS[area]` are checked. -/
def Range.valid (r : Range) : Bool :=
  r.start ≤ 99999 && r.count ≤ 9999 && 1 ≤ r.count && r.start + r.count ≤ areaSize r.area

/-- `policy.check_weintek_modbus` for one target: some allowlisted range covers the read. -/
def authorised (allow : List Range) (a : Area) (s c : Nat) : Bool :=
  allow.any fun r => r.covers a s c

/-- `weintek.modbus_address`: (function code, protocol address). -/
def modbusAddress : Area → Nat → Nat × Nat
  | .LB, s => (1, s)
  | .LW, s => (3, s)
  | .RW, s => (3, 9999 + s)

/-- The set of (function code, protocol address) cells one read touches. -/
def touches (a : Area) (s c : Nat) (fn addr : Nat) : Prop :=
  fn = (modbusAddress a s).1 ∧ (modbusAddress a s).2 ≤ addr ∧ addr < (modbusAddress a s).2 + c

/-- **Every cell read is a cell the allowlist names.** -/
theorem authorised_cells (allow : List Range) (a : Area) (s c fn addr : Nat)
    (h : authorised allow a s c = true) (ht : touches a s c fn addr) :
    ∃ r ∈ allow, touches r.area r.start r.count fn addr := by
  simp only [authorised, List.any_eq_true] at h
  obtain ⟨r, hr, hc⟩ := h
  refine ⟨r, hr, ?_⟩
  simp only [Range.covers, Bool.and_eq_true, beq_iff_eq, decide_eq_true_eq] at hc
  obtain ⟨⟨rfl, h1⟩, h2⟩ := hc
  rcases r with ⟨ra, rs, rc⟩
  cases ra <;> simp_all [touches, modbusAddress] <;> omega

/-- **Everything on the wire fits the request's 16-bit fields.** With a valid allowlist
and the tool's count bound, the start address, the count, and the last register read all
lie in 0..65535, so `struct.pack(">H", …)` never raises and never wraps. -/
theorem wire_fits_u16 (allow : List Range) (hv : ∀ r ∈ allow, r.valid = true)
    (a : Area) (s c : Nat) (hc1 : 1 ≤ c) (hc2 : c ≤ maxCount a)
    (h : authorised allow a s c = true) :
    (modbusAddress a s).2 + c ≤ 65536 ∧ c ≤ 65535 := by
  simp only [authorised, List.any_eq_true] at h
  obtain ⟨r, hr, hcov⟩ := h
  have := hv r hr
  simp only [Range.covers, Range.valid, Bool.and_eq_true, beq_iff_eq,
    decide_eq_true_eq] at hcov this
  obtain ⟨⟨rfl, _⟩, _⟩ := hcov
  rcases r with ⟨ra, rs, rc⟩
  cases ra <;> simp_all [modbusAddress, areaSize, maxCount] <;> omega

/-- **LW and RW never alias.** Both are read with function 03; every valid LW address maps
below 9999 and every RW address at or above it, so an LW allowlist entry can never
authorise reading RW memory, or the reverse. LB uses function 01, a separate table. -/
theorem lw_rw_disjoint (x y : Nat) (hx : x < areaSize .LW) :
    (modbusAddress .LW x).2 ≠ (modbusAddress .RW y).2 := by
  simp [modbusAddress, areaSize] at hx ⊢; omega

theorem lb_separate_table (x y : Nat) (a : Area) (ha : a ≠ .LB) :
    (modbusAddress .LB x).1 ≠ (modbusAddress a y).1 := by
  cases a <;> simp_all [modbusAddress]

/-- The tool's bounds are inside the protocol's own limits (2000 coils, 125 registers), and
the reply byte count fits the one-byte field it is carried in. -/
theorem counts_within_spec (a : Area) (c : Nat) (h : c ≤ maxCount a) :
    (a = .LB → c ≤ 2000 ∧ (c + 7) / 8 ≤ 255) ∧ (a ≠ .LB → c ≤ 125 ∧ 2 * c ≤ 255) := by
  cases a <;> simp_all [maxCount] <;> omega

/-- A covered read of a valid range stays inside its area, whatever the tool passed. -/
theorem covered_in_area (r : Range) (hv : r.valid = true) (a : Area) (s c : Nat)
    (h : r.covers a s c = true) : a = r.area ∧ s + c ≤ areaSize a := by
  simp only [Range.covers, Range.valid, Bool.and_eq_true, beq_iff_eq,
    decide_eq_true_eq] at h hv
  obtain ⟨⟨rfl, _⟩, _⟩ := h
  exact ⟨rfl, by omega⟩

end Omarchy.Modbus
