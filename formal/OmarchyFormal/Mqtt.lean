/-!
# MQTT topic filters, remaining length, packet ids

Models of `mcp/omarchy_hardware/mqtt_lite.py`:

* `topic_matches` / `filter_covers` — `check_ming_subscribe` lets a caller narrow an
  allowlisted subscribe filter (`plant/#` → `plant/line1/+`). The property that makes
  that safe is `filterCovers_sound`: a covered filter can never receive a message the
  allowlisted filter would not.
* `_encode_length` and the decoder in `Client._read_packet` — `decode_encode`.
* `Client._packet_id` — `nextPacketId_range`.

Topics are modelled as their `/`-separated levels, exactly as `str.split("/")`
produces them. The Python `$` checks look at the first character of the whole string,
which is the first character of the first level; `tests/differential.py` checks the
string-level wrappers below against the Python functions.
-/

namespace Omarchy.Mqtt

/-- First character of a level (`s[:1]` in Python). -/
def firstChar (s : String) : Option Char := s.toList.head?

/-- `topic.startswith("$")` -/
def isSys (s : String) : Bool := firstChar s == some '$'

/-- `topic_filter[:1] in ("+", "#")` -/
def isWildStart (s : String) : Bool := firstChar s == some '+' || firstChar s == some '#'

/-- The level loop of `topic_matches`, as a recursion over levels. -/
def matchesLevels : List String → List String → Bool
  | [], [] => true
  | [], _ :: _ => false
  | f :: fs, ts =>
    if f = "#" then true
    else match ts with
      | [] => false
      | t :: ts' => (f = "+" || f = t) && matchesLevels fs ts'

/-- `topic_matches(topic_filter, topic)` over split levels. -/
def topicMatches (filter topic : List String) : Bool :=
  if isSys (topic.headD "") && isWildStart (filter.headD "") then false
  else matchesLevels filter topic

/-- The level loop of `filter_covers`. -/
def coversLevels : List String → List String → Bool
  | [], [] => true
  | [], _ :: _ => false
  | a :: as, rs =>
    if a = "#" then true
    else match rs with
      | [] => false
      | r :: rs' => if r = "#" then false else (a = "+" || a = r) && coversLevels as rs'

/-- `filter_covers(allowed, requested)` over split levels. -/
def filterCovers (allowed requested : List String) : Bool :=
  if isSys (requested.headD "") && isWildStart (allowed.headD "") then false
  else coversLevels allowed requested

/-- String-level wrappers, the functions the differential test compares with Python. -/
def topicMatchesStr (filter topic : String) : Bool :=
  topicMatches (filter.splitOn "/") (topic.splitOn "/")

def filterCoversStr (allowed requested : String) : Bool :=
  filterCovers (allowed.splitOn "/") (requested.splitOn "/")

/-! ## Soundness of narrowing -/

theorem coversLevels_sound :
    ∀ (A R T : List String), coversLevels A R = true → matchesLevels R T = true →
      matchesLevels A T = true := by
  intro A
  induction A with
  | nil =>
    intro R T hc hm
    cases R with
    | nil => exact hm
    | cons _ _ => simp [coversLevels] at hc
  | cons a as ih =>
    intro R T hc hm
    by_cases ha : a = "#"
    · simp [matchesLevels, ha]
    cases R with
    | nil => simp [coversLevels, ha] at hc
    | cons r rs =>
      by_cases hr : r = "#"
      · simp [coversLevels, ha, hr] at hc
      cases T with
      | nil => simp [matchesLevels, hr] at hm
      | cons t ts =>
        simp only [coversLevels, ha, hr, ite_false, Bool.and_eq_true, Bool.or_eq_true,
          decide_eq_true_eq] at hc
        simp only [matchesLevels, hr, ite_false, Bool.and_eq_true, Bool.or_eq_true,
          decide_eq_true_eq] at hm
        simp only [matchesLevels, ha, ite_false, Bool.and_eq_true, Bool.or_eq_true,
          decide_eq_true_eq]
        refine ⟨?_, ih rs ts hc.2 hm.2⟩
        rcases hc.1 with h | h
        · exact Or.inl h
        · rcases hm.1 with h' | h'
          · exact Or.inl (h.trans h')
          · exact Or.inr (h.trans h')

theorem isWildStart_plus : isWildStart "+" = true := by decide
theorem isWildStart_hash : isWildStart "#" = true := by decide

/-- **Narrowing never widens.** If `filter_covers(allowed, requested)` holds, every topic
delivered for `requested` would also have been delivered for `allowed`, including the
`$`-topic rule (a leading wildcard never matches `$SYS/...`). -/
theorem filterCovers_sound (A R T : List String)
    (hc : filterCovers A R = true) (hm : topicMatches R T = true) :
    topicMatches A T = true := by
  unfold filterCovers at hc
  unfold topicMatches at hm ⊢
  split at hc
  · exact absurd hc (by simp)
  rename_i hsysR
  split at hm
  · exact absurd hm (by simp)
  rename_i hsysT
  have hlev := coversLevels_sound A R T hc hm
  split
  · -- The topic is a `$` topic and `allowed` starts with a wildcard.
    rename_i hbad
    simp only [Bool.and_eq_true] at hbad
    exfalso
    -- Then `requested` starts with neither `$` nor a wildcard ...
    have hRsys : isSys (R.headD "") = false := by
      cases h : isSys (R.headD "") <;> simp_all
    have hRwild : isWildStart (R.headD "") = false := by
      cases h : isWildStart (R.headD "") <;> simp_all
    -- ... so its first level is a literal that must equal the topic's first level.
    cases R with
    | nil => cases T <;> simp_all [matchesLevels, isSys, firstChar]
    | cons r rs =>
      cases T with
      | nil => simp [List.headD, isSys, firstChar] at hbad
      | cons t ts =>
        have hr1 : r ≠ "#" := by
          intro h; subst h; simp [List.headD, isWildStart_hash] at hRwild
        have hr2 : r ≠ "+" := by
          intro h; subst h; simp [List.headD, isWildStart_plus] at hRwild
        simp [matchesLevels, hr1, hr2] at hm
        simp [List.headD] at hRsys hbad
        rw [hm.1] at hRsys
        simp_all
  · exact hlev

theorem coversLevels_refl : ∀ A : List String, coversLevels A A = true := by
  intro A
  induction A with
  | nil => rfl
  | cons a as ih => by_cases h : a = "#" <;> simp [coversLevels, h, ih]

theorem isSys_not_wild (s : String) : ¬ (isSys s = true ∧ isWildStart s = true) := by
  unfold isSys isWildStart
  cases firstChar s with
  | none => simp
  | some c => by_cases h : c = '$' <;> simp_all

/-- Every allowlisted filter covers itself, so the allowlist is always usable as-is. -/
theorem filterCovers_refl (A : List String) : filterCovers A A = true := by
  unfold filterCovers
  have := isSys_not_wild (A.headD "")
  split
  · simp_all
  · exact coversLevels_refl A

/-- Spec 4.7.2 as implemented: a filter with a leading wildcard never matches a `$` topic. -/
theorem sys_topics_hidden (F T : List String)
    (hT : isSys (T.headD "") = true) (hF : isWildStart (F.headD "") = true) :
    topicMatches F T = false := by
  unfold topicMatches; rw [hT, hF]; rfl

/-! ## Remaining length (spec 2.2.3) -/

def maxRemainingLength : Nat := 268435455

/-- `_encode_length`: least-significant group first, bit 7 set on all but the last byte. -/
def encodeLength (n : Nat) : List Nat :=
  if n < 128 then [n] else (n % 128 + 128) :: encodeLength (n / 128)
termination_by n
decreasing_by omega

/-- The loop in `_read_packet` with `fuel` bytes left and the current multiplier
(`1 << shift`). `(byte & 0x7F) << shift` is `(byte % 128) * mult`; the groups never overlap,
so the Python `|=` is `+` (checked by `tests/differential.py`). -/
def decodeAux : Nat → Nat → List Nat → Option (Nat × List Nat)
  | 0, _, _ => none
  | _ + 1, _, [] => none
  | k + 1, mult, b :: bs =>
    if b < 128 then some ((b % 128) * mult, bs)
    else (decodeAux k (mult * 128) bs).map fun (x, rest) => ((b % 128) * mult + x, rest)

/-- Four bytes at most; a fourth byte with its continuation bit set is malformed. -/
def decodeLength (bs : List Nat) : Option (Nat × List Nat) := decodeAux 4 1 bs

theorem decodeAux_encode :
    ∀ (k n mult : Nat) (rest : List Nat), n < 128 ^ (k + 1) →
      decodeAux (k + 1) mult (encodeLength n ++ rest) = some (n * mult, rest) := by
  intro k
  induction k with
  | zero =>
    intro n mult rest h
    have hn : n < 128 := by simpa using h
    rw [encodeLength]; simp [hn, decodeAux, Nat.mod_eq_of_lt hn]
  | succ k ih =>
    intro n mult rest h
    rw [encodeLength]
    by_cases hn : n < 128
    · simp [hn, decodeAux, Nat.mod_eq_of_lt hn]
    · have hlt : n / 128 < 128 ^ (k + 1) := by
        rw [Nat.pow_succ] at h
        exact Nat.div_lt_of_lt_mul (by rw [Nat.mul_comm]; exact h)
      have hb : ¬ (n % 128 + 128 < 128) := by omega
      simp only [hn, ite_false, List.cons_append, decodeAux, hb]
      rw [ih (n / 128) (mult * 128) rest hlt]
      have hm : (n % 128 + 128) % 128 = n % 128 := by omega
      simp only [Option.map_some, hm, Option.some.injEq, Prod.mk.injEq, and_true]
      have := Nat.mod_add_div n 128
      grind

/-- **Round trip**: every length `_encode_length` accepts decodes back to itself, and the
decoder consumes exactly the encoded bytes. -/
theorem decode_encode (n : Nat) (rest : List Nat) (h : n ≤ maxRemainingLength) :
    decodeLength (encodeLength n ++ rest) = some (n, rest) := by
  have := decodeAux_encode 3 n 1 rest (by simp [maxRemainingLength] at h ⊢; omega)
  simpa [decodeLength] using this

theorem encodeLength_bytes : ∀ n, ∀ b ∈ encodeLength n, b < 256 := by
  intro n
  induction n using Nat.strongRecOn with
  | _ n ih =>
    intro b hb
    rw [encodeLength] at hb
    split at hb
    · simp at hb; omega
    · simp at hb
      rcases hb with h | h
      · omega
      · exact ih (n / 128) (by omega) b h

theorem encodeLength_length :
    ∀ k n, n < 128 ^ k → 1 ≤ k → (encodeLength n).length ≤ k := by
  intro k
  induction k with
  | zero => intro _ _ h; omega
  | succ k ih =>
    intro n h _
    rw [encodeLength]
    split
    · simp
    · rename_i hn
      simp only [List.length_cons]
      have hlt : n / 128 < 128 ^ k := by
        rw [Nat.pow_succ] at h
        exact Nat.div_lt_of_lt_mul (by rw [Nat.mul_comm]; exact h)
      cases k with
      | zero => simp at hlt; omega
      | succ k => have := ih (n / 128) hlt (by omega); omega

/-- The encoding of any accepted length fits the four bytes the decoder will read. -/
theorem encodeLength_fits (n : Nat) (h : n ≤ maxRemainingLength) :
    (encodeLength n).length ≤ 4 :=
  encodeLength_length 4 n (by simp [maxRemainingLength] at h ⊢; omega) (by omega)

/-! ## Packet identifiers (spec 2.2.1: non-zero, 16-bit) -/

/-- `self._next_id = self._next_id % 65535 + 1` -/
def nextPacketId (n : Nat) : Nat := n % 65535 + 1

theorem nextPacketId_range (n : Nat) : 1 ≤ nextPacketId n ∧ nextPacketId n ≤ 65535 := by
  unfold nextPacketId; omega

/-- From a valid id the counter steps by one and wraps 65535 → 1, never visiting 0. -/
theorem nextPacketId_cycle (n : Nat) (h1 : 1 ≤ n) (h2 : n ≤ 65535) :
    nextPacketId n = if n = 65535 then 1 else n + 1 := by
  unfold nextPacketId; split <;> omega

end Omarchy.Mqtt
