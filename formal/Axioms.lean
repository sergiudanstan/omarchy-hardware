import OmarchyFormal
/-! `lake env lean Axioms.lean` lists the axioms each headline theorem depends on. Only
`propext`, `Classical.choice` and `Quot.sound` are acceptable; `sorryAx` would mean an
unfinished proof. -/
#print axioms Omarchy.Mqtt.filterCovers_sound
#print axioms Omarchy.Mqtt.filterCovers_refl
#print axioms Omarchy.Mqtt.sys_topics_hidden
#print axioms Omarchy.Mqtt.decode_encode
#print axioms Omarchy.Mqtt.encodeLength_bytes
#print axioms Omarchy.Mqtt.encodeLength_fits
#print axioms Omarchy.Mqtt.nextPacketId_range
#print axioms Omarchy.Mqtt.nextPacketId_cycle
#print axioms Omarchy.Modbus.authorised_cells
#print axioms Omarchy.Modbus.wire_fits_u16
#print axioms Omarchy.Modbus.lw_rw_disjoint
#print axioms Omarchy.Modbus.lb_separate_table
#print axioms Omarchy.Modbus.counts_within_spec
#print axioms Omarchy.Modbus.covered_in_area
#print axioms Omarchy.Budget.budget_safe
