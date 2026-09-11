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

public sealed record OpcUaReadResult(
    string NodeId,
    string DataType,
    object Value);

public interface IOpcUaClient
{
    Task<HmiIdentity> IdentifyAsync(
        string endpoint,
        CancellationToken cancellationToken = default);

    Task<IReadOnlyList<OpcUaReadResult>> ReadAsync(
        string endpoint,
        IReadOnlyList<string> allowlistedNodes,
        CancellationToken cancellationToken = default);
}

public interface IMqttClient
{
    Task PublishAsync(
        string host,
        int port,
        string topic,
        string payload,
        CancellationToken cancellationToken = default);
}

public sealed class WeintekHmiAdapter(IOpcUaClient opcua, IMqttClient? mqtt = null) : IHardwareAdapter
{
    public DeviceFamily Family => DeviceFamily.WeintekHmi;

    public IReadOnlyList<HardwareOperation> Operations { get; } =
    [
        new("hmi.identify", OperationSafety.ReadOnly, false),
        new("opcua.read", OperationSafety.ReadOnly, false),
        new("opcua.write", OperationSafety.Destructive, true),
        new("mqtt.subscribe", OperationSafety.StateChanging, false),
        new("mqtt.publish", OperationSafety.Destructive, true),
    ];

    public async Task<HardwareInventory> InspectAsync(
        string identity,
        CancellationToken cancellationToken = default)
    {
        var hmi = await opcua.IdentifyAsync(identity, cancellationToken);
        var model = hmi.Model ?? hmi.Target.ToString();
        var firmware = hmi.Firmware is null ? null : $"firmware:{hmi.Firmware}";
        var capabilities = new List<string> { "hmi.identify", "opcua.read" };
        if (mqtt is not null)
            capabilities.Add("mqtt.publish");
        return new HardwareInventory(
            new CapabilityDevice(
                Family.ToString(),
                model,
                hmi.Endpoint,
                capabilities,
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
