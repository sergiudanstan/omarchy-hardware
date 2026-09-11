using System.Globalization;

namespace Omarchy.Hardware;

public sealed class JetsonAdapter(IFixedRemoteReader remote) : IHardwareAdapter
{
    private static readonly string[] CapabilityNames =
    [
        "jetson.inventory",
        "jetson.telemetry",
        "usb.enumerate",
        "serial.enumerate",
    ];

    public DeviceFamily Family => DeviceFamily.Jetson;

    public IReadOnlyList<HardwareOperation> Operations { get; } =
    [
        new("jetson.inventory", OperationSafety.ReadOnly, false),
        new("jetson.telemetry", OperationSafety.ReadOnly, false),
    ];

    public async Task<HardwareInventory> InspectAsync(
        string identity,
        CancellationToken cancellationToken = default)
    {
        var model = await ReadOptionalAsync(identity, "/proc/device-tree/model", cancellationToken);
        var release = await ReadOptionalAsync(identity, "/etc/os-release", cancellationToken);
        var kernel = await ReadOptionalAsync(identity, "/proc/sys/kernel/osrelease", cancellationToken);
        var jetsonRelease = await ReadOptionalAsync(identity, "/etc/nv_tegra_release", cancellationToken);
        var temperature = await ReadOptionalAsync(
            identity,
            "/sys/devices/virtual/thermal/thermal_zone0/temp",
            cancellationToken);
        var load = await ReadOptionalAsync(identity, "/proc/loadavg", cancellationToken);

        var values = ParseKeyValues(release);
        var version = values.GetValueOrDefault("VERSION_ID") ?? ParseJetsonVersion(jetsonRelease);
        return new HardwareInventory(
            new CapabilityDevice(
                Family.ToString(),
                Clean(model),
                identity,
                CapabilityNames,
                "reachable"),
            values.GetValueOrDefault("ID") ?? "jetson",
            values.GetValueOrDefault("PRETTY_NAME") ?? values.GetValueOrDefault("NAME"),
            version,
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

    private static string? ParseJetsonVersion(string? text)
    {
        var marker = "R";
        var start = text?.IndexOf(marker, StringComparison.OrdinalIgnoreCase) ?? -1;
        if (start < 0)
            return null;
        var version = text![start..].Split(',', StringSplitOptions.TrimEntries)[0];
        return version.Length > 1 ? version[1..] : null;
    }

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
