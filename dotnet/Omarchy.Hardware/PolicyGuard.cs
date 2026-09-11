namespace Omarchy.Hardware;

internal static class PolicyGuard
{
    public static string RequireAllowlisted(
        string identity,
        IReadOnlySet<string> allowlist,
        string label)
    {
        if (string.IsNullOrWhiteSpace(identity) || !allowlist.Contains(identity))
            throw new InvalidOperationException($"{label} is not in the allowlist.");
        return identity;
    }
}
