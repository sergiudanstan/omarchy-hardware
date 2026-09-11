namespace Omarchy.Hardware;

public enum MicrocontrollerVendor
{
    Arduino,
    Espressif,
    RaspberryPi,
    Adafruit,
    Teensy,
    Seeed,
    Waveshare,
    Stm32,
    M5Stack,
    Microbit,
    Nordic,
    Other,
}

public sealed record MicrocontrollerIdentity(
    string Port,
    string Vid,
    string Pid,
    string? Serial,
    string Family,
    string? SuggestedFqbn,
    MicrocontrollerVendor Vendor = MicrocontrollerVendor.Other);

public interface IMicrocontrollerCatalog
{
    Task<IReadOnlyList<MicrocontrollerIdentity>> EnumerateAsync(
        CancellationToken cancellationToken = default);
}

public sealed class MicrocontrollerAdapter(IMicrocontrollerCatalog catalog) : IHardwareAdapter
{
    private static readonly string[] AvailableOperationIds =
    [
        "board.list",
        "serial.open",
        "serial.read",
        "serial.write",
        "flash.compile",
        "flash.upload",
    ];
    private static readonly HashSet<string> AvailableOperations = [..AvailableOperationIds];

    public DeviceFamily Family => DeviceFamily.Microcontroller;

    public IReadOnlyList<HardwareOperation> Operations { get; } =
    [
        new("board.list", OperationSafety.ReadOnly, false),
        new("serial.open", OperationSafety.StateChanging, false),
        new("serial.read", OperationSafety.ReadOnly, false),
        new("serial.write", OperationSafety.Destructive, true),
        new("flash.compile", OperationSafety.ReadOnly, false),
        new("flash.upload", OperationSafety.Destructive, true),
    ];

    public async Task<HardwareInventory> InspectAsync(
        string identity,
        CancellationToken cancellationToken = default)
    {
        var boards = await catalog.EnumerateAsync(cancellationToken);
        var board = boards.FirstOrDefault(candidate =>
            string.Equals(candidate.Port, identity, StringComparison.Ordinal));
        if (board is null)
            throw new InvalidOperationException($"No microcontroller is connected at {identity}.");

        return new HardwareInventory(
            new CapabilityDevice(
                board.Family,
                board.SuggestedFqbn,
                board.Serial ?? board.Port,
                AvailableOperationIds,
                "reachable",
                RemoteText.Describe(Operations, AvailableOperations)),
            null,
            null,
            null,
            null,
            null,
            null,
            null);
    }
}
