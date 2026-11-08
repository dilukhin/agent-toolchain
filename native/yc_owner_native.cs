// Windows trust checks used by the pinned native bootstrap BEFORE Python loads.
// No cloud, approval issuance, shell execution, account/group/ACL repair or recovery.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using Microsoft.Win32.SafeHandles;

namespace YcOwnerNative {
    public sealed class Refusal : Exception {
        public Refusal(string code) : base(code) { }
    }
    public sealed class FilePin {
        public string Path { get; set; }
        public string Sha256 { get; set; }
        public long Size { get; set; }
    }
    public static class Guard {
        internal static void Need(bool value, string code) { if (!value) throw new Refusal(code); }
        internal static readonly HashSet<string> Trusted = new HashSet<string>(StringComparer.Ordinal) {
            "S-1-5-18", "S-1-5-32-544",
            "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
        };
        static readonly HashSet<string> DangerousGroups = new HashSet<string>(StringComparer.Ordinal) {
            "S-1-5-32-544", "S-1-5-32-547", "S-1-5-32-548", "S-1-5-32-549", "S-1-5-32-550",
            "S-1-5-32-551", "S-1-5-32-552", "S-1-5-32-556", "S-1-5-32-578"
        };
        static readonly HashSet<string> SafePrivileges = new HashSet<string>(StringComparer.Ordinal) {
            "SeChangeNotifyPrivilege", "SeIncreaseWorkingSetPrivilege", "SeTimeZonePrivilege",
            "SeShutdownPrivilege", "SeUndockPrivilege"
        };
        [StructLayout(LayoutKind.Sequential)] struct LsaAttributes {
            public uint Length; public IntPtr RootDirectory; public IntPtr ObjectName; public uint Attributes;
            public IntPtr SecurityDescriptor; public IntPtr SecurityQualityOfService;
        }
        [StructLayout(LayoutKind.Sequential)] struct LsaString {
            public ushort Length; public ushort MaximumLength; public IntPtr Buffer;
        }
        [DllImport("netapi32.dll", CharSet=CharSet.Unicode)] static extern uint NetUserGetLocalGroups(
            string server, string user, uint level, uint flags, out IntPtr buffer, uint preferred,
            out uint read, out uint total);
        [DllImport("netapi32.dll")] static extern uint NetApiBufferFree(IntPtr buffer);
        [DllImport("wtsapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool
            WTSQuerySessionInformation(IntPtr server, uint session, int kind, out IntPtr buffer, out uint bytes);
        [DllImport("wtsapi32.dll")] static extern void WTSFreeMemory(IntPtr buffer);
        [DllImport("advapi32.dll")] static extern uint LsaOpenPolicy(IntPtr system, ref LsaAttributes attrs,
            uint access, out IntPtr policy);
        [DllImport("advapi32.dll")] static extern uint LsaEnumerateAccountRights(IntPtr policy, byte[] sid,
            out IntPtr rights, out uint count);
        [DllImport("advapi32.dll")] static extern uint LsaFreeMemory(IntPtr buffer);
        [DllImport("advapi32.dll")] static extern uint LsaClose(IntPtr handle);
        [StructLayout(LayoutKind.Sequential)] struct SecurityAttributes {
            public uint Length; public IntPtr Descriptor; [MarshalAs(UnmanagedType.Bool)] public bool Inherit;
        }
        [DllImport("advapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool
            ConvertStringSecurityDescriptorToSecurityDescriptor(string sddl, uint revision, out IntPtr descriptor, IntPtr size);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool
            CreateDirectory(string path, ref SecurityAttributes attrs);
        [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr value);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern SafeFileHandle CreateFile(
            string path, uint access, uint share, ref SecurityAttributes attrs, uint creation, uint flags, IntPtr template);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool MoveFileEx(string from, string to, uint flags);

        public static string OwnerSid { get { return WindowsIdentity.GetCurrent().User.Value; } }
        public static void AssertInteractiveOwner(string agentSid) {
            Need(Process.GetCurrentProcess().SessionId != 0, "YC_OWNER_INTERACTIVE_SESSION_REQUIRED");
            Need(OwnerSid != agentSid, "YC_OWNER_DISTINCT_AGENT_REQUIRED");
            LocalAgentSubjects(agentSid);
        }
        public static void AssertStandardOwner(string agentSid) {
            AssertInteractiveOwner(agentSid);
            LocalAgentSubjects(OwnerSid);
        }
        static string SessionText(int kind) {
            IntPtr buffer = IntPtr.Zero; uint bytes;
            try {
                Need(WTSQuerySessionInformation(IntPtr.Zero, (uint)Process.GetCurrentProcess().SessionId,
                    kind, out buffer, out bytes) && bytes > 0 && bytes <= 1024 && bytes % 2 == 0,
                    "YC_OWNER_SESSION_LOGIN_UNAVAILABLE");
                string value = Marshal.PtrToStringUni(buffer, (int)bytes / 2).TrimEnd('\0');
                Need(value.Length > 0, "YC_OWNER_SESSION_LOGIN_UNAVAILABLE");
                return value;
            } finally { if (buffer != IntPtr.Zero) WTSFreeMemory(buffer); }
        }
        public static void AssertOwnerLoginSession(string agentSid) {
            AssertStandardOwner(agentSid);
            string user = SessionText(5), domain = SessionText(7); // WTSUserName, WTSDomainName
            Need(String.Equals(domain, Environment.MachineName, StringComparison.OrdinalIgnoreCase),
                "YC_OWNER_OWN_INTERACTIVE_LOGON_REQUIRED");
            string sid;
            try { sid = ((SecurityIdentifier)new NTAccount(domain, user).Translate(typeof(SecurityIdentifier))).Value; }
            catch { throw new Refusal("YC_OWNER_SESSION_LOGIN_UNAVAILABLE"); }
            Need(sid == OwnerSid, "YC_OWNER_OWN_INTERACTIVE_LOGON_REQUIRED");
        }
        static HashSet<string> LocalAgentSubjects(string agentSid) {
            SecurityIdentifier sid;
            try { sid = new SecurityIdentifier(agentSid); }
            catch { throw new Refusal("YC_OWNER_AGENT_SID_INVALID"); }
            string name;
            try { name = ((NTAccount)sid.Translate(typeof(NTAccount))).Value; }
            catch { throw new Refusal("YC_OWNER_AGENT_ACCOUNT_UNAVAILABLE"); }
            string[] parts = name.Split('\\');
            Need(parts.Length == 2 && String.Equals(parts[0], Environment.MachineName,
                StringComparison.OrdinalIgnoreCase), "YC_OWNER_LOCAL_AGENT_REQUIRED");
            HashSet<string> subjects = new HashSet<string>(StringComparer.Ordinal) {
                sid.Value, "S-1-1-0", "S-1-5-11", "S-1-5-4", "S-1-5-113"
            };
            IntPtr buffer = IntPtr.Zero;
            uint read, total;
            try {
                uint result = NetUserGetLocalGroups(null, parts[1], 0, 1, out buffer, 65536, out read, out total);
                Need(result == 0 && read == total && read <= 256, "YC_OWNER_AGENT_GROUPS_UNAVAILABLE");
                for (int i = 0; i < read; i++) {
                    string group = Marshal.PtrToStringUni(Marshal.ReadIntPtr(buffer, i * IntPtr.Size));
                    string value;
                    try { value = ((SecurityIdentifier)new NTAccount(group).Translate(typeof(SecurityIdentifier))).Value; }
                    catch { throw new Refusal("YC_OWNER_AGENT_GROUPS_UNAVAILABLE"); }
                    Need(!DangerousGroups.Contains(value), "YC_OWNER_STANDARD_AGENT_ACCOUNT_REQUIRED");
                    subjects.Add(value);
                }
            } finally { if (buffer != IntPtr.Zero) NetApiBufferFree(buffer); }
            Need(subjects.Contains("S-1-5-32-545"), "YC_OWNER_STANDARD_AGENT_ACCOUNT_REQUIRED");
            return subjects;
        }
        public static void AssertLocalAgentAccount(string agentSid) {
            HashSet<string> subjects = LocalAgentSubjects(agentSid);
            // Account membership is checked independently of a potentially filtered
            // process token. Also inspect explicitly assigned rights of every subject.
            LsaAttributes attrs = new LsaAttributes(); attrs.Length = (uint)Marshal.SizeOf(attrs);
            IntPtr policy = IntPtr.Zero;
            try {
                Need(LsaOpenPolicy(IntPtr.Zero, ref attrs, 0x800, out policy) == 0,
                    "YC_OWNER_AGENT_RIGHTS_UNAVAILABLE");
                foreach (string subject in subjects) {
                    SecurityIdentifier subjectSid = new SecurityIdentifier(subject);
                    byte[] bytes = new byte[subjectSid.BinaryLength]; subjectSid.GetBinaryForm(bytes, 0);
                    IntPtr rights = IntPtr.Zero; uint count;
                    try {
                        uint result = LsaEnumerateAccountRights(policy, bytes, out rights, out count);
                        if (result == 0xC0000034) continue; // this SID has no explicitly assigned rights
                        Need(result == 0 && count <= 256, "YC_OWNER_AGENT_RIGHTS_UNAVAILABLE");
                        int stride = Marshal.SizeOf(typeof(LsaString));
                        for (int i = 0; i < count; i++) {
                            LsaString item = (LsaString)Marshal.PtrToStructure(IntPtr.Add(rights, i * stride), typeof(LsaString));
                            Need(item.Length <= 512 && item.Length % 2 == 0, "YC_OWNER_AGENT_RIGHTS_UNAVAILABLE");
                            string right = Marshal.PtrToStringUni(item.Buffer, item.Length / 2);
                            Need(!right.EndsWith("Privilege", StringComparison.Ordinal) || SafePrivileges.Contains(right),
                                "YC_OWNER_STANDARD_AGENT_ACCOUNT_REQUIRED");
                        }
                    } finally { if (rights != IntPtr.Zero) LsaFreeMemory(rights); }
                }
            } finally { if (policy != IntPtr.Zero) LsaClose(policy); }
        }
        public static void WriteCapabilityAudit(string agentSid, string ownerSid, string nonce, string output) {
            WindowsPrincipal current = new WindowsPrincipal(WindowsIdentity.GetCurrent());
            Need(current.IsInRole(WindowsBuiltInRole.Administrator), "YC_OWNER_ADMIN_READ_REQUIRED");
            Need(ownerSid != agentSid && OwnerSid != agentSid, "YC_OWNER_DISTINCT_AGENT_REQUIRED");
            LocalAgentSubjects(ownerSid);
            AssertLocalAgentAccount(agentSid);
            Guid id; Need(Guid.TryParseExact(nonce, "D", out id) && id.ToString("D") == nonce,
                "YC_OWNER_CAPABILITY_NONCE_INVALID");
            output = LocalPath(output);
            Need(!File.Exists(output) && !Directory.Exists(output), "YC_OWNER_AUDIT_OUTPUT_EXISTS");
            string staging = LocalPath(System.IO.Path.Combine(System.IO.Path.GetDirectoryName(output), "~audit-" + Guid.NewGuid().ToString("N")));
            byte[] data = Encoding.ASCII.GetBytes("YC-OWNER-CAPS/1\n" + agentSid + "\n" + ownerSid + "\n" + nonce +
                "\n" + DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture) + "\nSTANDARD_LOCAL_ACCOUNT\n");
            using (FileLease parents = FileLease.AuditParentChain(System.IO.Path.GetDirectoryName(output), ownerSid)) {
                IntPtr descriptor = IntPtr.Zero;
                try {
                    Need(ConvertStringSecurityDescriptorToSecurityDescriptor("O:BAD:P(A;;FR;;;" + ownerSid +
                        ")(A;;FA;;;SY)(A;;FA;;;BA)", 1, out descriptor, IntPtr.Zero), "YC_OWNER_AUDIT_DACL_FAILED");
                    SecurityAttributes attrs = new SecurityAttributes(); attrs.Length = (uint)Marshal.SizeOf(attrs);
                    attrs.Descriptor = descriptor;
                    SafeFileHandle handle = CreateFile(staging, 0x40000000, 0, ref attrs, 1, 0x80, IntPtr.Zero);
                    Need(!handle.IsInvalid, "YC_OWNER_AUDIT_CREATE_FAILED");
                    using (FileStream stream = new FileStream(handle, FileAccess.Write)) { stream.Write(data, 0, data.Length); stream.Flush(true); }
                    Need(MoveFileEx(staging, output, 8), "YC_OWNER_AUDIT_PUBLISH_FAILED");
                } finally { if (descriptor != IntPtr.Zero) LocalFree(descriptor); }
            }
        }
        public static void VerifyCapabilityAudit(string agentSid, string nonce, string path) {
            Need(OwnerSid != agentSid, "YC_OWNER_DISTINCT_AGENT_REQUIRED");
            LocalAgentSubjects(agentSid);
            using (FileLease lease = new FileLease()) {
                FilePin pin = lease.HoldAuditFile(path);
                Need(pin.Size <= 4096, "YC_OWNER_CAPABILITY_SIZE_INVALID");
                string[] rows = Encoding.ASCII.GetString(File.ReadAllBytes(path)).Split('\n');
                Need(rows.Length == 7 && rows[0] == "YC-OWNER-CAPS/1" && rows[1] == agentSid && rows[2] == OwnerSid &&
                    rows[3] == nonce && rows[5] == "STANDARD_LOCAL_ACCOUNT" && rows[6] == "", "YC_OWNER_CAPABILITY_BINDING_INVALID");
                Guid id; Need(Guid.TryParseExact(nonce, "D", out id) && id.ToString("D") == nonce,
                    "YC_OWNER_CAPABILITY_NONCE_INVALID");
                DateTime issued;
                Need(DateTime.TryParseExact(rows[4], "o", CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out issued) &&
                    issued.Kind == DateTimeKind.Utc, "YC_OWNER_CAPABILITY_TIME_INVALID");
                double age = (DateTime.UtcNow - issued).TotalSeconds;
                Need(age >= 0 && age <= 180, "YC_OWNER_CAPABILITY_EXPIRED");
            }
        }
        internal static string LocalPath(string path) {
            Need(path != null && path.Length >= 3 && path.Length < 248 && path[1] == ':' &&
                Char.IsLetter(path[0]) && path[2] == '\\' && path.IndexOf(':', 2) < 0 &&
                path.IndexOf('/') < 0 && path.IndexOf('\0') < 0 && path.IndexOf('"') < 0,
                "YC_OWNER_LOCAL_PATH_REQUIRED");
            string full = System.IO.Path.GetFullPath(path);
            string result = full.Length == 3 ? full : full.TrimEnd('\\');
            string lexical = path.Length == 3 ? path : path.TrimEnd('\\');
            Need(String.Equals(result, lexical, StringComparison.OrdinalIgnoreCase),
                "YC_OWNER_CANONICAL_PATH_REQUIRED");
            foreach (string part in result.Substring(3).Split('\\'))
                Need(part != "." && part != ".." && !part.EndsWith(".") && !part.EndsWith(" "),
                    "YC_OWNER_CANONICAL_PATH_REQUIRED");
            return result;
        }
        public static string Hash(byte[] bytes) {
            using (SHA256 sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
        }
        // Only new directories may receive this ACL. Existing ACLs are never repaired.
        public static void CreatePrivateDirectory(string path) {
            path = LocalPath(path);
            Need(!Directory.Exists(path) && !File.Exists(path), "YC_OWNER_DESTINATION_EXISTS");
            string parent = System.IO.Path.GetDirectoryName(path);
            using (FileLease ancestors = FileLease.ParentChain(parent)) {
                SecurityIdentifier owner = WindowsIdentity.GetCurrent().User;
                string sddl = "O:" + owner.Value + "D:P(A;OICI;FA;;;" + owner.Value +
                    ")(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)";
                IntPtr descriptor = IntPtr.Zero;
                try {
                    Need(ConvertStringSecurityDescriptorToSecurityDescriptor(sddl, 1, out descriptor, IntPtr.Zero),
                        "YC_OWNER_PRIVATE_DACL_FAILED");
                    SecurityAttributes attrs = new SecurityAttributes();
                    attrs.Length = (uint)Marshal.SizeOf(attrs); attrs.Descriptor = descriptor; attrs.Inherit = false;
                    Need(CreateDirectory(path, ref attrs), "YC_OWNER_PRIVATE_DIRECTORY_CREATE_FAILED");
                } finally { if (descriptor != IntPtr.Zero) LocalFree(descriptor); }
            }
        }
    }

    public sealed class FileLease : IDisposable {
        [StructLayout(LayoutKind.Sequential)] struct FileInfo {
            public uint Attributes; public System.Runtime.InteropServices.ComTypes.FILETIME Creation, Access, Write;
            public uint Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
        }
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern SafeFileHandle CreateFile(
            string path, uint access, uint share, IntPtr attrs, uint creation, uint flags, IntPtr template);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool GetFileInformationByHandle(SafeFileHandle handle, out FileInfo info);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern uint GetFinalPathNameByHandle(
            SafeFileHandle handle, StringBuilder path, uint length, uint flags);
        [DllImport("advapi32.dll")] static extern uint GetSecurityInfo(SafeFileHandle handle, uint kind, uint wanted,
            IntPtr owner, IntPtr group, IntPtr dacl, IntPtr sacl, out IntPtr descriptor);
        [DllImport("advapi32.dll")] static extern uint GetSecurityDescriptorLength(IntPtr descriptor);
        [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr buffer);
        readonly List<IDisposable> held = new List<IDisposable>();
        readonly HashSet<string> directories = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        readonly Dictionary<string, FilePin> files = new Dictionary<string, FilePin>(StringComparer.OrdinalIgnoreCase);
        readonly HashSet<string> trees = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        readonly string additionalOwner;
        bool closed;
        long totalBytes;

        public FileLease() { }
        FileLease(string owner) { additionalOwner = owner; }
        void CheckAcl(SafeFileHandle handle, bool ancestor, bool administrative) {
            IntPtr descriptor = IntPtr.Zero;
            try {
                Guard.Need(GetSecurityInfo(handle, 1, 5, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, out descriptor) == 0,
                    "YC_OWNER_FILE_SECURITY_UNAVAILABLE");
                uint length = GetSecurityDescriptorLength(descriptor);
                Guard.Need(length > 0 && length <= 65536, "YC_OWNER_FILE_SECURITY_UNAVAILABLE");
                byte[] raw = new byte[length]; Marshal.Copy(descriptor, raw, 0, (int)length);
                RawSecurityDescriptor sd = new RawSecurityDescriptor(raw, 0);
                string owner = Guard.OwnerSid;
                Guard.Need(sd.Owner != null && (sd.Owner.Value == owner || sd.Owner.Value == additionalOwner || Guard.Trusted.Contains(sd.Owner.Value)),
                    "YC_OWNER_UNTRUSTED_FILE_OWNER");
                if (administrative) Guard.Need(sd.Owner.Value == "S-1-5-18" || sd.Owner.Value == "S-1-5-32-544",
                    "YC_OWNER_ADMIN_AUDIT_REQUIRED");
                Guard.Need(sd.DiscretionaryAcl != null, "YC_OWNER_NULL_DACL");
                uint dangerous = ancestor ? 0x500D0040U : 0x500D0156U;
                foreach (GenericAce entry in sd.DiscretionaryAcl) {
                    CommonAce ace = entry as CommonAce;
                    Guard.Need(ace != null && !ace.IsCallback &&
                        (ace.AceQualifier == AceQualifier.AccessAllowed || ace.AceQualifier == AceQualifier.AccessDenied),
                        "YC_OWNER_UNSUPPORTED_ACE");
                    if (ace.AceQualifier != AceQualifier.AccessAllowed || (ace.AceFlags & AceFlags.InheritOnly) != 0) continue;
                    string sid = ace.SecurityIdentifier.Value;
                    if (sid != owner && sid != additionalOwner && !Guard.Trusted.Contains(sid))
                        Guard.Need(((uint)ace.AccessMask & dangerous) == 0, "YC_OWNER_AGENT_WRITABLE_PATH");
                }
            } finally { if (descriptor != IntPtr.Zero) LocalFree(descriptor); }
        }
        SafeFileHandle Open(string path, bool directory, bool ancestor, bool administrative = false, bool exclusive = false) {
            Guard.Need(!closed, "YC_OWNER_LEASE_CLOSED");
            // FILE_SHARE_READ only: writes, replacement and deletion cannot race a live lease.
            SafeFileHandle handle = CreateFile(path, exclusive ? 0xC0020000U : (directory ? 0x20080U : 0x80020000U), exclusive ? 0U : 1U,
                IntPtr.Zero, 3, 0x00200000U | (directory ? 0x02000000U : 0), IntPtr.Zero);
            Guard.Need(!handle.IsInvalid, "YC_OWNER_LEASE_OPEN_FAILED");
            try {
                FileInfo info;
                Guard.Need(GetFileInformationByHandle(handle, out info), "YC_OWNER_FILE_INFO_UNAVAILABLE");
                Guard.Need((info.Attributes & 0x400) == 0 && ((info.Attributes & 0x10) != 0) == directory,
                    "YC_OWNER_REPARSE_OR_FILE_TYPE");
                Guard.Need(directory || info.Links == 1, "YC_OWNER_HARDLINK_FORBIDDEN");
                StringBuilder final = new StringBuilder(1024);
                uint length = GetFinalPathNameByHandle(handle, final, 1024, 0);
                Guard.Need(length > 0 && length < 1024, "YC_OWNER_FINAL_PATH_UNAVAILABLE");
                string resolved = final.ToString();
                if (resolved.StartsWith("\\\\?\\", StringComparison.Ordinal)) resolved = resolved.Substring(4);
                Guard.Need(String.Equals(resolved.TrimEnd('\\'), path.TrimEnd('\\'), StringComparison.OrdinalIgnoreCase),
                    "YC_OWNER_PATH_REPLACED");
                CheckAcl(handle, ancestor, administrative);
                return handle;
            } catch { handle.Dispose(); throw; }
        }
        void HoldDirectory(string path, bool ancestor) {
            path = Guard.LocalPath(path);
            if (!directories.Contains(path)) { held.Add(Open(path, true, ancestor)); directories.Add(path); }
            else if (!ancestor) { using (SafeFileHandle handle = Open(path, true, false)) { } }
        }
        void Ancestors(string path) {
            DirectoryInfo current = new DirectoryInfo(path);
            List<string> chain = new List<string>();
            while (current != null) { chain.Add(current.FullName); current = current.Parent; }
            chain.Reverse();
            foreach (string item in chain) HoldDirectory(item, true);
        }
        public static FileLease ParentChain(string parent) {
            FileLease result = new FileLease();
            try { result.Ancestors(Guard.LocalPath(parent)); return result; }
            catch { result.Dispose(); throw; }
        }
        internal static FileLease AuditParentChain(string parent, string owner) {
            FileLease result = new FileLease(owner);
            try { result.Ancestors(Guard.LocalPath(parent)); return result; }
            catch { result.Dispose(); throw; }
        }
        public FilePin HoldFile(string path, string expectedHash) {
            return HoldFileCore(path, expectedHash, false);
        }
        public void HoldRoot(string path) {
            path = Guard.LocalPath(path); Ancestors(path); HoldDirectory(path, false);
        }
        public void HoldLock(string path, string expectedHash) {
            path = Guard.LocalPath(path);
            Guard.Need(!files.ContainsKey(path), "YC_OWNER_DUPLICATE_LOCK");
            Ancestors(System.IO.Path.GetDirectoryName(path));
            SafeFileHandle handle = Open(path, false, false, false, true);
            FileStream stream = null;
            try {
                stream = new FileStream(handle, FileAccess.ReadWrite);
                Guard.Need(stream.Length > 0 && stream.Length <= 4096, "YC_OWNER_LOCK_MARKER_INVALID");
                string digest;
                using (SHA256 sha = SHA256.Create()) digest = BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
                Guard.Need(digest == expectedHash, "YC_OWNER_LOCK_MARKER_INVALID");
                stream.Position = 0;
                held.Add(stream);
                files.Add(path, new FilePin { Path=path, Sha256=digest, Size=stream.Length });
            } catch { if (stream != null) stream.Dispose(); else handle.Dispose(); throw; }
        }
        internal FilePin HoldAuditFile(string path) { return HoldFileCore(path, null, true); }
        FilePin HoldFileCore(string path, string expectedHash, bool administrative) {
            path = Guard.LocalPath(path);
            Guard.Need(!files.ContainsKey(path) && files.Count < 10000, "YC_OWNER_FILE_COUNT_LIMIT");
            Ancestors(System.IO.Path.GetDirectoryName(path));
            SafeFileHandle handle = Open(path, false, false, administrative);
            FileStream stream = null;
            try {
                stream = new FileStream(handle, FileAccess.Read);
                Guard.Need(stream.Length <= 256L * 1024 * 1024 && totalBytes + stream.Length <= 512L * 1024 * 1024,
                    "YC_OWNER_FILE_SIZE_LIMIT");
                string digest;
                using (SHA256 sha = SHA256.Create()) digest = BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
                Guard.Need(expectedHash == null || String.Equals(digest, expectedHash, StringComparison.Ordinal),
                    "YC_OWNER_FILE_HASH_MISMATCH");
                stream.Position = 0;
                FilePin pin = new FilePin { Path = path, Sha256 = digest, Size = stream.Length };
                files.Add(path, pin); totalBytes += stream.Length; held.Add(stream);
                return pin;
            } catch { if (stream != null) stream.Dispose(); else handle.Dispose(); throw; }
        }
        public FilePin[] HoldTree(string root) {
            root = Guard.LocalPath(root); Ancestors(root); HoldDirectory(root, false);
            int before = files.Count;
            Stack<string> todo = new Stack<string>(); todo.Push(root);
            while (todo.Count != 0) {
                string directory = todo.Pop(); HoldDirectory(directory, false);
                string[] entries = Directory.GetFileSystemEntries(directory);
                Guard.Need(entries.Length <= 10000 && directories.Count <= 10000, "YC_OWNER_FILE_COUNT_LIMIT");
                Array.Sort(entries, StringComparer.OrdinalIgnoreCase);
                foreach (string entry in entries) {
                    FileAttributes flags = File.GetAttributes(entry);
                    Guard.Need((flags & FileAttributes.ReparsePoint) == 0, "YC_OWNER_REPARSE_OR_FILE_TYPE");
                    if ((flags & FileAttributes.Directory) != 0) todo.Push(entry);
                    else HoldFile(entry, null);
                }
            }
            Guard.Need(files.Count > before, "YC_OWNER_EMPTY_TREE");
            trees.Add(root);
            List<FilePin> pins = new List<FilePin>(files.Values);
            pins.Sort((a, b) => StringComparer.OrdinalIgnoreCase.Compare(a.Path, b.Path));
            return pins.ToArray();
        }
        public ManagedChild StartPython(string python, string entry, string config, string cwd, string agentSid) {
            Guard.Need(!closed, "YC_OWNER_LEASE_CLOSED");
            Guard.AssertStandardOwner(agentSid);
            python = Guard.LocalPath(python); entry = Guard.LocalPath(entry);
            config = Guard.LocalPath(config); cwd = Guard.LocalPath(cwd);
            Guard.Need(files.ContainsKey(python) && files.ContainsKey(entry) && files.ContainsKey(config)
                && directories.Contains(cwd) && trees.Contains(System.IO.Path.GetDirectoryName(python))
                && System.IO.Path.GetFileName(python) == "python.exe", "YC_OWNER_UNHELD_PROCESS_INPUT");
            // Recheck cwd as a protected directory, not merely an ancestor.
            HoldDirectory(cwd, false);
            ManagedChild child = ManagedChild.Start(python, entry, config, cwd, agentSid);
            held.Add(child); // killed BEFORE any interpreter/payload lease is released
            return child;
        }
        public void Dispose() {
            if (closed) return; closed = true;
            for (int i = held.Count - 1; i >= 0; i--) held[i].Dispose();
            held.Clear();
        }
    }

    // No pipe/approval interface: only the pinned native composition root calls
    // this after holding every interpreter/payload/config input under FileLease.
    public sealed class ManagedChild : IDisposable {
        [StructLayout(LayoutKind.Sequential)] struct SecurityAttributes {
            public uint Length; public IntPtr Descriptor; [MarshalAs(UnmanagedType.Bool)] public bool Inherit;
        }
        [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] struct StartupInfo {
            public uint Size; public string Reserved, Desktop, Title;
            public uint X, Y, XSize, YSize, XChars, YChars, Fill, Flags;
            public ushort Show, ReservedSize; public IntPtr ReservedData, Input, Output, Error;
        }
        [StructLayout(LayoutKind.Sequential)] struct ProcessInfo { public IntPtr Process, Thread; public uint Pid, Tid; }
        [StructLayout(LayoutKind.Sequential)] struct BasicLimits {
            public long ProcessTime, JobTime; public uint Flags; public UIntPtr MinWorking, MaxWorking;
            public uint ActiveProcesses; public UIntPtr Affinity; public uint Priority, Scheduling;
        }
        [StructLayout(LayoutKind.Sequential)] struct IoCounters { public ulong A, B, C, D, E, F; }
        [StructLayout(LayoutKind.Sequential)] struct ExtendedLimits {
            public BasicLimits Basic; public IoCounters Io;
            public UIntPtr ProcessMemory, JobMemory, PeakProcess, PeakJob;
        }
        [StructLayout(LayoutKind.Sequential)] struct Accounting {
            public long UserTime, KernelTime, PeriodUser, PeriodKernel;
            public uint Faults, TotalProcesses, ActiveProcesses, TerminatedProcesses;
        }
        [DllImport("advapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool
            ConvertStringSecurityDescriptorToSecurityDescriptor(string sddl, uint revision, out IntPtr descriptor, IntPtr size);
        [DllImport("advapi32.dll", SetLastError=true)] static extern bool SetKernelObjectSecurity(IntPtr handle, uint info, IntPtr descriptor);
        [DllImport("advapi32.dll", SetLastError=true)] static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);
        [DllImport("advapi32.dll", SetLastError=true)] static extern bool GetSecurityDescriptorDacl(
            IntPtr descriptor, out bool present, out IntPtr acl, out bool defaulted);
        [DllImport("advapi32.dll", SetLastError=true)] static extern bool SetTokenInformation(
            IntPtr token, int kind, ref IntPtr data, uint size);
        [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool CreateProcess(
            string application, StringBuilder command, ref SecurityAttributes processAttrs, ref SecurityAttributes threadAttrs,
            bool inherit, uint flags, IntPtr environment, string cwd, ref StartupInfo startup, out ProcessInfo info);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern IntPtr CreateJobObject(IntPtr attrs, string name);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool SetInformationJobObject(IntPtr job, int kind, ref ExtendedLimits data, uint size);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
        [DllImport("kernel32.dll", SetLastError=true)] static extern uint ResumeThread(IntPtr thread);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool TerminateProcess(IntPtr process, uint code);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool TerminateJobObject(IntPtr job, uint code);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool QueryInformationJobObject(
            IntPtr job, int kind, out Accounting data, uint size, IntPtr returned);
        [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
        [DllImport("kernel32.dll")] static extern bool GetExitCodeProcess(IntPtr process, out uint code);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr value);
        IntPtr job, process;
        public uint Pid { get; private set; }
        bool closed;

        static IntPtr Descriptor(string agentSid, string allowed, string denied) {
            IntPtr result;
            Guard.Need(ConvertStringSecurityDescriptorToSecurityDescriptor("O:" + Guard.OwnerSid +
                "D:P(D;;" + denied + ";;;" + agentSid + ")(A;;" + allowed + ";;;" + agentSid +
                ")(A;;GA;;;" + Guard.OwnerSid + ")(A;;GA;;;SY)(A;;GA;;;BA)", 1, out result, IntPtr.Zero),
                "YC_OWNER_PROCESS_DESCRIPTOR_FAILED");
            return result;
        }
        static void ProtectProcessAndToken(IntPtr target, IntPtr processSd, IntPtr tokenSd, IntPtr childDefaultSd) {
            Guard.Need(SetKernelObjectSecurity(target, 4, processSd), "YC_OWNER_PROCESS_DACL_FAILED");
            IntPtr token = IntPtr.Zero;
            try {
                Guard.Need(OpenProcessToken(target, childDefaultSd == IntPtr.Zero ? 0x40008U : 0x40088U, out token), "YC_OWNER_PROCESS_TOKEN_UNAVAILABLE");
                Guard.Need(SetKernelObjectSecurity(token, 4, tokenSd), "YC_OWNER_TOKEN_DACL_FAILED");
                if (childDefaultSd != IntPtr.Zero) {
                    bool present, defaulted; IntPtr acl;
                    Guard.Need(GetSecurityDescriptorDacl(childDefaultSd, out present, out acl, out defaulted)
                        && present && acl != IntPtr.Zero, "YC_OWNER_DEFAULT_DACL_UNAVAILABLE");
                    // TOKEN_DEFAULT_DACL is one pointer. Future default-created
                    // processes/threads/pipes cannot expose YC credentials to the agent.
                    Guard.Need(SetTokenInformation(token, 6, ref acl, (uint)IntPtr.Size), "YC_OWNER_DEFAULT_DACL_FAILED");
                }
            } finally { if (token != IntPtr.Zero) CloseHandle(token); }
        }
        static string Quote(string path) {
            // Canonical local file paths cannot contain quotes or end with '\'.
            Guard.Need(path.IndexOf('"') < 0 && !path.EndsWith("\\"), "YC_OWNER_PROCESS_PATH_INVALID");
            return "\"" + path + "\"";
        }
        public static void SealParent(string agentSid) {
            Guard.AssertStandardOwner(agentSid);
            IntPtr processSd = IntPtr.Zero, tokenSd = IntPtr.Zero;
            try {
                processSd = Descriptor(agentSid, "0x1000", "0x1fefff");
                tokenSd = Descriptor(agentSid, "0x8", "0xf01f7");
                ProtectProcessAndToken(GetCurrentProcess(), processSd, tokenSd, IntPtr.Zero);
            } finally {
                if (tokenSd != IntPtr.Zero) LocalFree(tokenSd);
                if (processSd != IntPtr.Zero) LocalFree(processSd);
            }
        }
        internal static ManagedChild Start(string python, string entry, string config, string cwd, string agentSid) {
            ManagedChild result = new ManagedChild();
            IntPtr processSd = IntPtr.Zero, tokenSd = IntPtr.Zero, threadSd = IntPtr.Zero, defaultSd = IntPtr.Zero, environment = IntPtr.Zero;
            ProcessInfo child = new ProcessInfo();
            try {
                processSd = Descriptor(agentSid, "0x1000", "0x1fefff");
                tokenSd = Descriptor(agentSid, "0x8", "0xf01f7");
                threadSd = Descriptor(agentSid, "0", "GA");
                defaultSd = Descriptor(agentSid, "0", "GA");
                ProtectProcessAndToken(GetCurrentProcess(), processSd, tokenSd, IntPtr.Zero);
                result.job = CreateJobObject(IntPtr.Zero, null);
                Guard.Need(result.job != IntPtr.Zero, "YC_OWNER_JOB_CREATE_FAILED");
                ExtendedLimits limits = new ExtendedLimits();
                limits.Basic.Flags = 0x2000 | 0x8 | 0x200; // kill on close, active count, job memory
                limits.Basic.ActiveProcesses = 8;
                limits.JobMemory = new UIntPtr(1024UL * 1024 * 1024);
                Guard.Need(SetInformationJobObject(result.job, 9, ref limits, (uint)Marshal.SizeOf(limits)),
                    "YC_OWNER_JOB_LIMITS_FAILED");
                string windows = Guard.LocalPath(Environment.GetFolderPath(Environment.SpecialFolder.Windows));
                // No inherited Python/YC/proxy/config/plugin variables, handles or
                // search paths. Credentials are entered later in the protected UI.
                string block = "PATH=" + windows + "\\System32\0SystemRoot=" + windows +
                    "\0TEMP=" + cwd + "\0TMP=" + cwd + "\0USERPROFILE=" + cwd + "\0\0";
                environment = Marshal.StringToHGlobalUni(block);
                SecurityAttributes processAttrs = new SecurityAttributes { Length=(uint)Marshal.SizeOf(typeof(SecurityAttributes)), Descriptor=processSd };
                SecurityAttributes threadAttrs = new SecurityAttributes { Length=(uint)Marshal.SizeOf(typeof(SecurityAttributes)), Descriptor=threadSd };
                StartupInfo startup = new StartupInfo(); startup.Size = (uint)Marshal.SizeOf(startup);
                Guard.Need(CreateProcess(python, new StringBuilder(Quote(python) + " -I -S -B " + Quote(entry) + " " + Quote(config)),
                    ref processAttrs, ref threadAttrs, false, 0x4 | 0x400 | 0x04000000, environment, cwd, ref startup, out child),
                    "YC_OWNER_PROCESS_CREATE_FAILED");
                result.process = child.Process; result.Pid = child.Pid;
                // No Python instruction runs before token protection and job assignment.
                ProtectProcessAndToken(child.Process, processSd, tokenSd, defaultSd);
                Guard.Need(AssignProcessToJobObject(result.job, child.Process), "YC_OWNER_JOB_ASSIGN_FAILED");
                Guard.Need(ResumeThread(child.Thread) == 1, "YC_OWNER_PROCESS_RESUME_FAILED");
                return result;
            } catch {
                if (child.Process != IntPtr.Zero) TerminateProcess(child.Process, 70);
                result.Dispose(); throw;
            } finally {
                if (child.Thread != IntPtr.Zero) CloseHandle(child.Thread);
                if (environment != IntPtr.Zero) Marshal.FreeHGlobal(environment);
                if (threadSd != IntPtr.Zero) LocalFree(threadSd);
                if (defaultSd != IntPtr.Zero) LocalFree(defaultSd);
                if (tokenSd != IntPtr.Zero) LocalFree(tokenSd);
                if (processSd != IntPtr.Zero) LocalFree(processSd);
            }
        }
        public uint Wait(uint milliseconds) {
            Guard.Need(!closed && milliseconds > 0 && milliseconds <= 600000, "YC_OWNER_PROCESS_WAIT_INVALID");
            uint status = WaitForSingleObject(process, milliseconds);
            if (status == 258) { Dispose(); throw new Refusal("YC_OWNER_PROCESS_TIMEOUT"); }
            Guard.Need(status == 0, "YC_OWNER_PROCESS_WAIT_FAILED");
            uint code; Guard.Need(GetExitCodeProcess(process, out code), "YC_OWNER_PROCESS_EXIT_UNAVAILABLE");
            return code;
        }
        public void Dispose() {
            if (closed) return; closed = true;
            if (job != IntPtr.Zero) {
                bool terminated = TerminateJobObject(job, 70);
                Stopwatch watch = Stopwatch.StartNew();
                Accounting data;
                bool drained = false;
                while (terminated && watch.ElapsedMilliseconds < 5000) {
                    if (!QueryInformationJobObject(job, 1, out data, (uint)Marshal.SizeOf(typeof(Accounting)), IntPtr.Zero)) break;
                    if (data.ActiveProcesses == 0) { drained = true; break; }
                    System.Threading.Thread.Sleep(10);
                }
                // Never release executable/config leases under a live child. An
                // unconfirmed kernel shutdown is fatal to this native host.
                if (!drained) Environment.FailFast("YC_OWNER_JOB_SHUTDOWN_UNCONFIRMED");
                CloseHandle(job); job = IntPtr.Zero;
            }
            if (process != IntPtr.Zero) { WaitForSingleObject(process, 5000); CloseHandle(process); process = IntPtr.Zero; }
        }
    }
}
