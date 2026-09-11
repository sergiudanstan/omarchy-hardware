namespace Omarchy.Hardware;

public enum WeintekTarget
{
    Cmt,
    Mt,
}

public sealed record HmiIdentity(
    WeintekTarget Target,
    string Endpoint,
    string? Model,
    string? Firmware);

public interface IReadOnlyHmiClient
{
    Task<HmiIdentity> IdentifyAsync(
        WeintekTarget target,
        string endpoint,
        CancellationToken cancellationToken = default);

    Task<IReadOnlyList<PlcTag>> ReadTagsAsync(
        HmiIdentity identity,
        IReadOnlyList<string> allowlistedAddresses,
        CancellationToken cancellationToken = default);
}

public sealed class WeintekHmiAdapter(IReadOnlyHmiClient client, WeintekTarget target)
    : IHardwareAdapter
{
    public DeviceFamily Family => DeviceFamily.WeintekHmi;

    public IReadOnlyList<HardwareOperation> Operations { get; } =
    [
        new("hmi.identify", OperationSafety.ReadOnly, false),
        new("hmi.read_tags", OperationSafety.ReadOnly, false),
        new("hmi.write_tags", OperationSafety.Destructive, true),
        new("plc.read_tags", OperationSafety.ReadOnly, false),
        new("plc.write_tags", OperationSafety.Destructive, true),
    ];

    public async Task<HardwareInventory> InspectAsync(
        string identity,
        CancellationToken cancellationToken = default)
    {
        var hmi = await client.IdentifyAsync(target, identity, cancellationToken);
        var model = hmi.Model ?? target.ToString();
        var firmware = hmi.Firmware is null ? null : $"firmware:{hmi.Firmware}";
        return new HardwareInventory(
            new CapabilityDevice(
                Family.ToString(),
                model,
                hmi.Endpoint,
                ["hmi.identify", "hmi.read_tags"],
                "reachable",
                Operations.Select(op => new CapabilityOperation(op.Id, op.Safety, false)).ToArray()),
            "weintek",
            model,
            firmware,
            null,
            null,
            null,
            null);
    }
}
