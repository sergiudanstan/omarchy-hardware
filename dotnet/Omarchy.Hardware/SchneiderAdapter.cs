namespace Omarchy.Hardware;

public enum SchneiderTarget
{
    ModiconM221,
    ModiconM340,
    ModiconM580,
}

public sealed record SchneiderIdentity(
    SchneiderTarget Target,
    string Endpoint,
    string? Model,
    string? Firmware);

public interface IReadOnlySchneiderClient
{
    Task<SchneiderIdentity> IdentifyAsync(
        SchneiderTarget target,
        string endpoint,
        CancellationToken cancellationToken = default);

    Task<IReadOnlyList<PlcTag>> ReadTagsAsync(
        SchneiderIdentity identity,
        IReadOnlyList<string> allowlistedAddresses,
        CancellationToken cancellationToken = default);
}

public sealed class SchneiderAdapter(
    IReadOnlySchneiderClient client,
    SchneiderTarget target,
    IReadOnlySet<string> allowedEndpoints) : IHardwareAdapter
{
    public DeviceFamily Family => DeviceFamily.SchneiderPlc;

    public IReadOnlyList<HardwareOperation> Operations { get; } =
    [
        new("plc.discover", OperationSafety.ReadOnly, false),
        new("plc.read", OperationSafety.ReadOnly, false),
        new("plc.write", OperationSafety.Destructive, true),
    ];

    public async Task<HardwareInventory> InspectAsync(
        string identity,
        CancellationToken cancellationToken = default)
    {
        identity = PolicyGuard.RequireAllowlisted(identity, allowedEndpoints, "Schneider endpoint");
        var plc = await client.IdentifyAsync(target, identity, cancellationToken);
        var model = plc.Model ?? target.ToString();
        var firmware = plc.Firmware is null ? null : $"firmware:{plc.Firmware}";
        return new HardwareInventory(
            new CapabilityDevice(
                Family.ToString(),
                model,
                plc.Endpoint,
                [],
                "reachable",
                RemoteText.Describe(Operations, new HashSet<string>())),
            "schneider",
            model,
            firmware,
            null,
            null,
            null,
            null);
    }
}
