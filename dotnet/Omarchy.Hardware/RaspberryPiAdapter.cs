using System.Globalization;

namespace Omarchy.Hardware;

public sealed record RemoteReadResult(bool Succeeded, string Output);

public interface IFixedRemoteReader
{
    Task<RemoteReadResult> ReadAsync(
        string identity,
        string path,
        CancellationToken cancellationToken = default);
}

public sealed class RaspberryPiAdapter(IFixedRemoteReader remote) : IHardwareAdapter
{
    private static readonly string[] CapabilityNames =
    [
        "pi.status",
        "gpio.read",
        "gpio.list",
        "gpio.set_mode",
        "gpio.write",
    ];

    public DeviceFamily Family => DeviceFamily.RaspberryPi;

    public IReadOnlyList<HardwareOperation> Operations { get; } =
    [
        new("pi.inventory", OperationSafety.ReadOnly, false),
        new("pi.status", OperationSafety.ReadOnly, false),
        new("gpio.read", OperationSafety.ReadOnly, false),
        new("gpio.list", OperationSafety.ReadOnly, false),
        new("gpio.set_mode", OperationSafety.StateChanging, true),
        new("gpio.write", OperationSafety.Destructive, true),
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

        var values = ParseKeyValues(release);
        return new HardwareInventory(
            new CapabilityDevice(
                Family.ToString(),
                Clean(model),
                identity,
                CapabilityNames,
                "reachable",
                Operations.Select(op => new CapabilityOperation(op.Id, op.Safety, true)).ToArray()),
            values.GetValueOrDefault("ID"),
            values.GetValueOrDefault("PRETTY_NAME") ?? values.GetValueOrDefault("NAME"),
            values.GetValueOrDefault("VERSION_ID"),
            Clean(kernel),
            null,
            ParseTemperature(temperature),
            ParseLoad(load));
    }

    private async Task<string?> ReadOptionalAsync(
        string identity,
        string path,
        CancellationToken cancellationToken)
    {
        var result = await remote.ReadAsync(identity, path, cancellationToken);
        return result.Succeeded ? result.Output[..Math.Min(result.Output.Length, 16_384)] : null;
    }

    private static string? Clean(string? value) =>
        value?.Trim().Trim('\0') is { Length: > 0 } cleaned ? cleaned : null;

    private static double? ParseTemperature(string? value) =>
        double.TryParse(value?.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out var millidegrees)
            && millidegrees is >= -100_000 and <= 200_000
            ? millidegrees / 1000
            : null;

    private static double? ParseLoad(string? value) =>
        double.TryParse(
            value?.Split(' ', StringSplitOptions.RemoveEmptyEntries).FirstOrDefault(),
            CultureInfo.InvariantCulture,
            out var load) && load >= 0
            ? load
            : null;

    private static Dictionary<string, string> ParseKeyValues(string? text)
    {
        var values = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (var line in text?.Split('\n', StringSplitOptions.RemoveEmptyEntries) ?? [])
        {
            var separator = line.IndexOf('=');
            if (separator <= 0)
                continue;
            var key = line[..separator];
            var value = line[(separator + 1)..].Trim().Trim('"');
            if (key.All(c => char.IsLetterOrDigit(c) || c == '_'))
                values[key] = value;
        }
        return values;
    }
}
