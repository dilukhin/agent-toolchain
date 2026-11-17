using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Security.Principal;
public sealed class Yc59GroupRow {
    public string name;
    public string sid;
}
public static class Yc59ReadGroups {
    [DllImport("netapi32.dll", CharSet=CharSet.Unicode)]
    static extern uint NetUserGetLocalGroups(string server, string user, uint level, uint flags,
        out IntPtr buffer, uint preferred, out uint read, out uint total);
    [DllImport("netapi32.dll")]
    static extern uint NetApiBufferFree(IntPtr buffer);
    public static string Account(string expectedSid) {
        string name = ((NTAccount)new SecurityIdentifier(expectedSid).Translate(typeof(NTAccount))).Value;
        string[] parts = name.Split('\\');
        if (parts.Length != 2 || !String.Equals(parts[0], Environment.MachineName,
                StringComparison.OrdinalIgnoreCase)) throw new Exception("YC59_LOCAL_ACCOUNT_REQUIRED");
        return name;
    }
    public static Yc59GroupRow[] Read(string expectedSid) {
        string name = Account(expectedSid);
        IntPtr buffer = IntPtr.Zero;
        uint read, total;
        try {
            // Same read-only API, flags and group-name resolution as LocalAgentSubjects.
            uint rc = NetUserGetLocalGroups(null, name.Split('\\')[1], 0, 1,
                out buffer, 65536, out read, out total);
            if (rc != 0 || read != total || read > 256) throw new Exception("YC59_GROUP_ENUMERATION_REFUSED");
            List<Yc59GroupRow> result = new List<Yc59GroupRow>();
            for (int i = 0; i < read; i++) {
                string group = Marshal.PtrToStringUni(Marshal.ReadIntPtr(buffer, i * IntPtr.Size));
                string sid = ((SecurityIdentifier)new NTAccount(group).Translate(typeof(SecurityIdentifier))).Value;
                result.Add(new Yc59GroupRow {name=group, sid=sid});
            }
            return result.ToArray();
        } finally { if (buffer != IntPtr.Zero) NetApiBufferFree(buffer); }
    }
}