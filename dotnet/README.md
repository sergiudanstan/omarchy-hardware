# C# orchestration layer

This directory is the planned high-level hardware service. C# owns typed
capability records, adapter orchestration, JSON contracts, policy decisions,
and future MCP/Omarchy integration. `IHardwareAdapter` and
`AdapterRegistry` define the typed boundary for Raspberry Pi, Jetson,
microcontroller, and Siemens adapters. Inventory records include
`CapabilityOperation` entries so unsupported work is visible without a
missing method. Low-level parsing and device primitives
belong in the C library under `native/`.

`RaspberryPiAdapter` is read-only and depends on `IFixedRemoteReader`; the
transport must enforce the existing host allowlist, SSH host-key policy,
fixed paths, output bounds, and timeouts. It intentionally does not expose
arbitrary remote commands.

`JetsonAdapter` uses the same bounded reader only for Jetson-safe inventory
paths. It is intentionally separate from Raspberry Pi GPIO and currently
exposes read-only inventory and telemetry capabilities.

`MicrocontrollerAdapter` defines the native identity and operation boundary for
Arduino, ESP32, and RP2040 boards. Flashing remains destructive and must be
implemented by the existing compile-token, FQBN, board-preflight, confirmation,
and audit gates.

`SiemensAdapter` is a read-only-first contract for LOGO! and S7-1200 targets.
It requires typed, allowlisted tag addresses through `IReadOnlyPlcClient`;
protocol selection, licensing, firmware compatibility, and any future writes
must be resolved before an implementation is enabled.

`SchneiderAdapter` is the same read-only-first shape for Modicon M221/M340/M580
targets. Transport and addressing are not selected yet; no Schneider protocol
is enabled.

`WeintekHmiAdapter` covers cMT/MT EasyBuilder panels as an HMI family. PLC tags
exposed through the panel still require allowlisted addresses. EasyAccess and
project download are out of scope.

The current Omarchy plugin still uses its stable Python MCP entry point while
this backend is introduced incrementally. Build with the .NET SDK when
available:

```bash
dotnet build dotnet/Omarchy.Hardware/Omarchy.Hardware.csproj
```

The runtime may be installed without the SDK; in that case source review and
the native C test suite remain available, but C# compilation cannot be run.
