namespace Omarchy.Hardware;

public enum SiemensTarget
{
    Logo,
    S7_1200,
}

public sealed record PlcTag(
    string Address,
    string DataType,
    object Value);

public sealed record PlcIdentity(
    SiemensTarget Target,
    string Endpoint,
    string? Model,
    string? Firmware);

public interface IReadOnlyPlcClient
{
    Task<PlcIdentity> IdentifyAsync(
        SiemensTarget target,
        string endpoint,
        CancellationToken cancellationToken = default);

    Task<IReadOnlyList<PlcTag>> ReadTagsAsync(
        PlcIdentity identity,
        IReadOnlyList<string> allowlistedAddresses,
        CancellationToken cancellationToken = default);
}

public sealed class SiemensAdapter(IReadOnlyPlcClient client, SiemensTarget target) : IHardwareAdapter
{
    public DeviceFamily Family => target == SiemensTarget.Logo
        ? DeviceFamily.SiemensLogo
        : DeviceFamily.SiemensS7;

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
                "reachable"),
            "siemens",
            model,
            firmware,
            null,
            null,
            null,
            null);
    }
}
