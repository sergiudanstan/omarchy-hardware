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

    /// <summary>
    /// Refuses an unsigned, unencrypted session unless it was accepted deliberately.
    /// </summary>
    /// <remarks>
    /// The allowlist answers "may this machine be reached"; it says nothing about
    /// whether anyone on the path can read or forge what is sent. Both questions
    /// are asked here so neither can be skipped by an adapter that only remembered
    /// the first one.
    /// </remarks>
    public static void RequireSecureTransport(
        bool isInsecure,
        string label,
        bool allowInsecure = false)
    {
        if (isInsecure && !allowInsecure)
            throw new InvalidOperationException(
                $"{label} would be reached without encryption or authentication. " +
                "Configure transport security, or opt in explicitly.");
    }
}
