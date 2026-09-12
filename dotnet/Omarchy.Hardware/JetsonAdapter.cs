namespace Omarchy.Hardware;

public sealed class JetsonAdapter(IFixedRemoteReader remote, IReadOnlySet<string> allowedIdentities)
    : IHardwareAdapter
{
    private static readonly HashSet<string> AvailableOperations = ["jetson.inventory", "jetson.status"];

    public DeviceFamily Family => DeviceFamily.Jetson;

    public IReadOnlyList<HardwareOperation> Operations { get; } =
    [
        new("jetson.inventory", OperationSafety.ReadOnly, false),
        new("jetson.status", OperationSafety.ReadOnly, false),
        new("jetson.telemetry", OperationSafety.ReadOnly, false),
    ];

    public async Task<HardwareInventory> InspectAsync(
        string identity,
        CancellationToken cancellationToken = default)
    {
        identity = PolicyGuard.RequireAllowlisted(identity, allowedIdentities, "Jetson host");
        var model = await ReadOptionalAsync(identity, "/proc/device-tree/model", cancellationToken);
        var release = await ReadOptionalAsync(identity, "/etc/os-release", cancellationToken);
        var kernel = await ReadOptionalAsync(identity, "/proc/sys/kernel/osrelease", cancellationToken);
        var jetsonRelease = await ReadOptionalAsync(identity, "/etc/nv_tegra_release", cancellationToken);
        var temperature = await ReadOptionalAsync(
            identity,
            "/sys/devices/virtual/thermal/thermal_zone0/temp",
            cancellationToken);
        var load = await ReadOptionalAsync(identity, "/proc/loadavg", cancellationToken);

        var values = RemoteText.ParseKeyValues(release);
        var version = values.GetValueOrDefault("VERSION_ID") ?? ParseJetsonVersion(jetsonRelease);
        return new HardwareInventory(
            new CapabilityDevice(
                Family.ToString(),
                RemoteText.Clean(model),
                identity,
                [],
                "reachable",
                RemoteText.Describe(Operations, AvailableOperations)),
            values.GetValueOrDefault("ID") ?? "jetson",
            values.GetValueOrDefault("PRETTY_NAME") ?? values.GetValueOrDefault("NAME"),
            version,
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

    private static string? ParseJetsonVersion(string? text)
    {
        var marker = "R";
        var start = text?.IndexOf(marker, StringComparison.OrdinalIgnoreCase) ?? -1;
        if (start < 0)
            return null;
        var version = text![start..].Split(',', StringSplitOptions.TrimEntries)[0];
        return version.Length > 1 ? version[1..] : null;
    }
}
