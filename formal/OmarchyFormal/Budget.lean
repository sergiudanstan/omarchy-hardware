/-!
# Rolling budgets

Model of `policy.RollingBudget.charge` for one target. The same class backs the serial
write budget (bytes), the actuation budget (GPIO, OPC UA and MQTT writes), the flash
budget (uploads per hour) and the MING write budget.

The implementation keeps only events that are still inside the window, measured from the
latest `now`. The guarantee a user relies on is stronger than the check it makes: for
**every** window of length `window` — not only the ones ending at a charge — the units
admitted inside it never exceed `limit`. `budget_safe` proves that for any sequence of
charges whose timestamps are non-decreasing, which `time.monotonic()` guarantees.

Timestamps are integers here (Python uses a float clock); counts are natural numbers
(every caller passes `len(...)` or the default 1).
-/

namespace Omarchy.Budget

structure Event where
  t : Int
  n : Nat

/-- `now - t < self.window` -/
def live (w now : Int) (e : Event) : Bool := decide (now - e.t < w)

def total (es : List Event) : Nat := (es.map Event.n).sum

/-- `RollingBudget.charge`: `none` is the `RATE_LIMITED` error, `some` the new event list. -/
def charge (limit : Nat) (w : Int) (state : List Event) (now : Int) (c : Nat) :
    Option (List Event) :=
  let events := state.filter (live w now)
  if total events + c > limit then none else some (events ++ [⟨now, c⟩])

/-- Run a sequence of charges, returning the stored state and every admitted event. -/
def run (limit : Nat) (w : Int) : List Event → List (Int × Nat) → List Event × List Event
  | state, [] => (state, [])
  | state, (now, c) :: rest =>
    match charge limit w state now c with
    | none => run limit w state rest
    | some state' =>
      let r := run limit w state' rest
      (r.1, ⟨now, c⟩ :: r.2)

/-- Units admitted in the half-open window `(a - w, a]`. -/
def inWindow (w a : Int) (e : Event) : Bool := decide (a - w < e.t ∧ e.t ≤ a)

def windowTotal (w a : Int) (log : List Event) : Nat := total (log.filter (inWindow w a))

/-! ### Lemmas about sums of filtered lists -/

theorem total_append (xs ys : List Event) : total (xs ++ ys) = total xs + total ys := by
  simp [total]

theorem total_filter_mono (p q : Event → Bool) (h : ∀ e, p e = true → q e = true) :
    ∀ xs : List Event, total (xs.filter p) ≤ total (xs.filter q) := by
  intro xs
  induction xs with
  | nil => simp [total]
  | cons x xs ih =>
    simp only [List.filter_cons]
    by_cases hp : p x = true
    · have hq := h x hp
      simp only [hp, hq, ite_true]
      simp only [total, List.map_cons, List.sum_cons] at ih ⊢
      omega
    · by_cases hq : q x = true
      · simp only [hp, hq, ite_true]
        simp only [total, List.map_cons, List.sum_cons] at ih ⊢
        simp at hp; simp; omega
      · simp at hp hq; simp [hp, hq]; exact ih

theorem filter_congr' (p q : Event → Bool) (xs : List Event)
    (h : ∀ e ∈ xs, p e = q e) : xs.filter p = xs.filter q := by
  induction xs with
  | nil => rfl
  | cons x xs ih =>
    simp only [List.filter_cons]
    rw [h x (by simp), ih (fun e he => h e (by simp [he]))]

/-! ### The invariant -/

/-- `state` is exactly the admitted events still live at `last`, and nothing admitted is
later than `last`, and every window so far is within the limit. -/
structure Inv (limit : Nat) (w : Int) (log state : List Event) (last : Int) : Prop where
  state_eq : state = log.filter (live w last)
  past : ∀ e ∈ log, e.t ≤ last
  safe : ∀ a, windowTotal w a log ≤ limit

theorem inv_step (limit : Nat) (w : Int) (hw : 0 < w) (log state : List Event)
    (last now : Int) (c : Nat) (hle : last ≤ now) (inv : Inv limit w log state last)
    (state' : List Event) (hc : charge limit w state now c = some state') :
    Inv limit w (log ++ [⟨now, c⟩]) state' now := by
  unfold charge at hc
  simp only at hc
  split at hc
  · cases hc
  rename_i hok
  simp only [Option.some.injEq] at hc
  subst hc
  -- Re-filtering the pruned state at `now` is filtering the whole log at `now`.
  have hre : (state.filter (live w now)) = log.filter (live w now) := by
    rw [inv.state_eq, List.filter_filter]
    apply filter_congr'
    intro e he
    have := inv.past e he
    simp only [live]
    cases h : decide (now - e.t < w) <;> simp_all <;> omega
  constructor
  · rw [hre, List.filter_append]
    simp [live, hw]
  · intro e he
    simp only [List.mem_append, List.mem_singleton] at he
    rcases he with he | rfl
    · exact Int.le_trans (inv.past e he) hle
    · exact Int.le_refl _
  · intro a
    unfold windowTotal
    rw [List.filter_append, total_append]
    by_cases hin : inWindow w a ⟨now, c⟩ = true
    · -- The new event counts in this window; so does nothing older than `now - w`.
      have hsub : total (log.filter (inWindow w a)) ≤ total (log.filter (live w now)) := by
        simp only [inWindow, decide_eq_true_eq] at hin
        apply total_filter_mono
        intro e he
        simp only [inWindow, live, decide_eq_true_eq] at he ⊢
        omega
      rw [hre] at hok
      simp only [List.filter_cons, List.filter_nil, hin, ite_true]
      simp only [total, List.map_cons, List.map_nil, List.sum_cons, List.sum_nil] at hsub hok ⊢
      omega
    · simp only [List.filter_cons, List.filter_nil]
      simp only [Bool.not_eq_true] at hin
      simp only [hin]
      have := inv.safe a
      simp only [windowTotal] at this
      simp [total] at this ⊢
      omega

/-- `run` preserves the invariant over a whole sequence whose times never go backwards. -/
theorem run_inv (limit : Nat) (w : Int) (hw : 0 < w) :
    ∀ (reqs : List (Int × Nat)) (log state : List Event) (last : Int),
      Inv limit w log state last →
      (∀ r ∈ reqs, last ≤ r.1) →
      reqs.Pairwise (fun x y => x.1 ≤ y.1) →
      ∀ a, windowTotal w a (log ++ (run limit w state reqs).2) ≤ limit := by
  intro reqs
  induction reqs with
  | nil => intro log state last inv _ _ a; simpa [run] using inv.safe a
  | cons r rest ih =>
    intro log state last inv hlast hsorted a
    obtain ⟨now, c⟩ := r
    have hnow : last ≤ now := hlast (now, c) (by simp)
    have hrest : ∀ r ∈ rest, now ≤ r.1 := by
      intro r hr; exact (List.pairwise_cons.mp hsorted).1 r hr
    have hsorted' := (List.pairwise_cons.mp hsorted).2
    simp only [run]
    split
    · -- rejected: nothing changes, and `last` still bounds what is left
      exact ih log state last inv (fun r hr => Int.le_trans hnow (hrest r hr)) hsorted' a
    · rename_i state' hc
      have inv' := inv_step limit w hw log state last now c hnow inv state' hc
      have := ih (log ++ [⟨now, c⟩]) state' now inv' hrest hsorted' a
      simpa using this

/-- **The budget holds for every window.** Starting empty, for any charges with
non-decreasing timestamps and a positive window, the units admitted in any interval
`(a - window, a]` total at most `limit`. -/
theorem budget_safe (limit : Nat) (w : Int) (hw : 0 < w) (reqs : List (Int × Nat))
    (hsorted : reqs.Pairwise (fun x y => x.1 ≤ y.1)) (a : Int) :
    windowTotal w a (run limit w [] reqs).2 ≤ limit := by
  cases reqs with
  | nil => simp [run, windowTotal, total]
  | cons r rest =>
    have inv0 : Inv limit w [] [] r.1 :=
      ⟨by simp, by simp, by intro a; simp [windowTotal, total]⟩
    have hall : ∀ x ∈ r :: rest, r.1 ≤ x.1 := by
      intro x hx
      simp only [List.mem_cons] at hx
      rcases hx with rfl | hx
      · exact Int.le_refl _
      · exact (List.pairwise_cons.mp hsorted).1 x hx
    simpa using run_inv limit w hw (r :: rest) [] [] r.1 inv0 hall hsorted a

end Omarchy.Budget
