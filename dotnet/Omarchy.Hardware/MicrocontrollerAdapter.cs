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
    public DeviceFamily Family => DeviceFamily.Microcontroller;

    public IReadOnlyList<HardwareOperation> Operations { get; } =
    [
        new("microcontroller.enumerate", OperationSafety.ReadOnly, false),
        new("microcontroller.serial_read", OperationSafety.ReadOnly, false),
        new("microcontroller.serial_write", OperationSafety.StateChanging, true),
        new("microcontroller.flash", OperationSafety.Destructive, true),
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
                ["microcontroller.enumerate", "serial.read", "serial.write", "flash"],
                "reachable",
                Operations.Select(op => new CapabilityOperation(op.Id, op.Safety, true)).ToArray()),
            null,
            null,
            null,
            null,
            null,
            null,
            null);
    }
}
