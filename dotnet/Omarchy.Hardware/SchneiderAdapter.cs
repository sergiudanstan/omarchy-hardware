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

public sealed class SchneiderAdapter(IReadOnlySchneiderClient client, SchneiderTarget target)
    : IHardwareAdapter
{
    public DeviceFamily Family => DeviceFamily.SchneiderPlc;

    public IReadOnlyList<HardwareOperation> Operations { get; } =
    [
        new("plc.identify", OperationSafety.ReadOnly, false),
        new("plc.read_tags", OperationSafety.ReadOnly, false),
        new("plc.write_tags", OperationSafety.Destructive, true),
    ];

    public async Task<HardwareInventory> InspectAsync(
        string identity,
        CancellationToken cancellationToken = default)
    {
        var plc = await client.IdentifyAsync(target, identity, cancellationToken);
        var model = plc.Model ?? target.ToString();
        var firmware = plc.Firmware is null ? null : $"firmware:{plc.Firmware}";
        return new HardwareInventory(
            new CapabilityDevice(
                Family.ToString(),
                model,
                plc.Endpoint,
                ["plc.identify", "plc.read_tags"],
                "reachable",
                Operations.Select(op => new CapabilityOperation(op.Id, op.Safety, false)).ToArray()),
            "schneider",
            model,
            firmware,
            null,
            null,
            null,
            null);
    }
}
