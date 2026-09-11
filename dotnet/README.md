# C# orchestration layer

This directory is the planned high-level hardware service. C# owns typed
capability records, adapter orchestration, JSON contracts, policy decisions,
and future MCP/Omarchy integration. `IHardwareAdapter` and
`AdapterRegistry` define the typed boundary for Raspberry Pi, Jetson,
microcontroller, and Siemens adapters. Low-level parsing and device primitives
belong in the C library under `native/`.

`RaspberryPiAdapter` is read-only and depends on `IFixedRemoteReader`; the
transport must enforce the existing host allowlist, SSH host-key policy,
fixed paths, output bounds, and timeouts. It intentionally does not expose
arbitrary remote commands.

The current Omarchy plugin still uses its stable Python MCP entry point while
this backend is introduced incrementally. Build with the .NET SDK when
available:

```bash
dotnet build dotnet/Omarchy.Hardware/Omarchy.Hardware.csproj
```

The runtime may be installed without the SDK; in that case source review and
the native C test suite remain available, but C# compilation cannot be run.
