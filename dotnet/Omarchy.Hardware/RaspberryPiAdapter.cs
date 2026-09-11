namespace Omarchy.Hardware;

public sealed class RaspberryPiAdapter(IFixedRemoteReader remote) : IHardwareAdapter
{
    private static readonly string[] AvailableOperationIds =
    [
        "pi.inventory",
        "pi.status",
        "gpio.list",
        "gpio.read",
        "gpio.set_mode",
        "gpio.write",
    ];
    private static readonly HashSet<string> AvailableOperations = [..AvailableOperationIds];

    public DeviceFamily Family => DeviceFamily.RaspberryPi;

    public IReadOnlyList<HardwareOperation> Operations { get; } =
    [
        new("pi.inventory", OperationSafety.ReadOnly, false),
        new("pi.status", OperationSafety.ReadOnly, false),
        new("gpio.list", OperationSafety.ReadOnly, false),
        new("gpio.read", OperationSafety.ReadOnly, false),
        new("gpio.set_mode", OperationSafety.StateChanging, true),
        new("gpio.write", OperationSafety.Destructive, true),
        new("gpio.pwm", OperationSafety.StateChanging, true),
        new("gpio.spi", OperationSafety.StateChanging, true),
        new("gpio.i2c", OperationSafety.StateChanging, true),
    ];

    public async Task<HardwareInventory> InspectAsync(
        string identity,
        CancellationToken cancellationToken = default)
    {
        var model = await ReadOptionalAsync(identity, "/proc/device-tree/model", cancellationToken);
        var release = await ReadOptionalAsync(identity, "/etc/os-release", cancellationToken);
        var kernel = await ReadOptionalAsync(identity, "/proc/sys/kernel/osrelease", cancellationToken);
        var temperature = await ReadOptionalAsync(
            identity,
            "/sys/class/thermal/thermal_zone0/temp",
            cancellationToken);
        var load = await ReadOptionalAsync(identity, "/proc/loadavg", cancellationToken);

        var values = RemoteText.ParseKeyValues(release);
        return new HardwareInventory(
            new CapabilityDevice(
                Family.ToString(),
                RemoteText.Clean(model),
                identity,
                AvailableOperationIds,
                "reachable",
                RemoteText.Describe(Operations, AvailableOperations)),
            values.GetValueOrDefault("ID"),
            values.GetValueOrDefault("PRETTY_NAME") ?? values.GetValueOrDefault("NAME"),
            values.GetValueOrDefault("VERSION_ID"),
            RemoteText.Clean(kernel),
            null,
            RemoteText.ParseTemperature(temperature),
            RemoteText.ParseLoad(load));
    }

    private async Task<string?> ReadOptionalAsync(
        string identity,
        string path,
        CancellationToken cancellationToken)
    {
        var result = await remote.ReadAsync(identity, path, cancellationToken);
        return result.Succeeded ? result.Output[..Math.Min(result.Output.Length, 16_384)] : null;
    }
}
