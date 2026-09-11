namespace Omarchy.Hardware;

public enum OmronTarget
{
    Cp,
    Cj,
    Nj,
    Nx,
}

public sealed record OmronIdentity(
    OmronTarget Target,
    string Endpoint,
    string? Model,
    string? Firmware);

public interface IReadOnlyOmronClient
{
    Task<OmronIdentity> IdentifyAsync(
        OmronTarget target,
        string endpoint,
        CancellationToken cancellationToken = default);

    Task<IReadOnlyList<PlcTag>> ReadTagsAsync(
        OmronIdentity identity,
        IReadOnlyList<string> allowlistedAddresses,
        CancellationToken cancellationToken = default);
}

public sealed class OmronAdapter(IReadOnlyOmronClient client, OmronTarget target) : IHardwareAdapter
{
    public DeviceFamily Family => DeviceFamily.OmronPlc;

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
            "omron",
            model,
            firmware,
            null,
            null,
            null,
            null);
    }
}
