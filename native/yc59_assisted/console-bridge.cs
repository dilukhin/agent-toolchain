using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

// A local owner's input assistant. It never chooses or enters an approval.
public static class Yc59ConsoleR11 {
    public sealed class Metrics {
        public string code="";
        public string stage="initial";
        public int win32_error;
        public int buffer_width, buffer_height, cursor_x, cursor_y;
        public int expected_prompt_characters;
        public bool prompt_snapshot_stable, prompt_matched;
        public uint requested_characters, read_characters, pending_before_write, requested_records, written_records;
        public uint remaining_records, sampled_records, sampled_key_down, sampled_key_up, sampled_mouse, sampled_resize, sampled_focus, sampled_other;
        public int sample_error;
    }
    static Metrics metrics=new Metrics();
    [StructLayout(LayoutKind.Sequential)] public struct XY { public short X,Y; }
    [StructLayout(LayoutKind.Sequential)] struct Rect { public short L,T,R,B; }
    [StructLayout(LayoutKind.Sequential)] struct Info {
        public XY Size, Cursor; public ushort Attr; public Rect Window; public XY Maximum;
    }
    [StructLayout(LayoutKind.Explicit, Size=20, CharSet=CharSet.Unicode)] struct Input {
        [FieldOffset(0)] public ushort Type;
        [FieldOffset(4)] public int Down;
        [FieldOffset(8)] public ushort Repeat;
        [FieldOffset(10)] public ushort VirtualKey;
        [FieldOffset(12)] public ushort Scan;
        [FieldOffset(14)] public char Character;
        [FieldOffset(16)] public uint State;
    }
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool FreeConsole();
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool AttachConsole(uint pid);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern SafeFileHandle CreateFile(string p,uint a,uint s,IntPtr sa,uint c,uint f,IntPtr t);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool GetConsoleScreenBufferInfo(SafeFileHandle h,out Info i);
    [DllImport("kernel32.dll", EntryPoint="ReadConsoleOutputCharacterW", ExactSpelling=true, SetLastError=true)]
    static extern bool ReadConsoleOutputCharacter(SafeFileHandle h,IntPtr s,uint n,XY p,out uint read);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern bool WriteConsoleInput(SafeFileHandle h,Input[] b,uint n,out uint written);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool GetNumberOfConsoleInputEvents(SafeFileHandle h,out uint n);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern bool PeekConsoleInput(SafeFileHandle h,[Out] Input[] b,uint n,out uint read);
    [DllImport("kernel32.dll", SetLastError=true)] public static extern bool WaitNamedPipe(string name,uint timeout);
    static SafeFileHandle input, output;
    static void Need(bool b,string code) { if(!b) { metrics.code=code;throw new Exception(code); } }
    static void Api(bool b,string code) { if(!b) metrics.win32_error=Marshal.GetLastWin32Error(); Need(b,code); }
    public static Metrics Snapshot() {
        if(input!=null && !input.IsInvalid && !input.IsClosed) {
            uint pending;
            if(GetNumberOfConsoleInputEvents(input,out pending)) {
                metrics.remaining_records=pending;
                if(pending>0) {
                    uint sample=Math.Min(pending,128);var records=new Input[sample];uint count;
                    if(PeekConsoleInput(input,records,sample,out count)) {
                        metrics.sampled_records=count;
                        for(int j=0;j<count;j++) {
                            if(records[j].Type==1) {if(records[j].Down!=0) metrics.sampled_key_down++;else metrics.sampled_key_up++;}
                            else if(records[j].Type==2) metrics.sampled_mouse++;
                            else if(records[j].Type==4) metrics.sampled_resize++;
                            else if(records[j].Type==16) metrics.sampled_focus++;
                            else metrics.sampled_other++;
                        }
                    } else metrics.sample_error=Marshal.GetLastWin32Error();
                    Array.Clear(records,0,records.Length);
                }
            } else metrics.sample_error=Marshal.GetLastWin32Error();
        }
        return metrics;
    }
    public static void Attach(int pid) {
        metrics.stage="attach";
        Need(pid>0 && input==null,"YC59_CONSOLE_TARGET_INVALID");
        FreeConsole();
        Api(AttachConsole((uint)pid),"YC59_CONSOLE_ATTACH_FAILED");
        input=CreateFile("CONIN$",0xC0000000,3,IntPtr.Zero,3,0,IntPtr.Zero);
        output=CreateFile("CONOUT$",0x80000000,3,IntPtr.Zero,3,0,IntPtr.Zero);
        Api(!input.IsInvalid&&!output.IsInvalid,"YC59_CONSOLE_HANDLES_FAILED");
    }
    // Bounded in-memory screen inspection. Never write screen contents to a log.
    static Info ScreenInfo() {
        metrics.stage="read.screen-info";
        Info i; Api(GetConsoleScreenBufferInfo(output,out i),"YC59_CONSOLE_SCREEN_UNAVAILABLE");
        Need(i.Size.X>0&&i.Size.Y>0&&i.Cursor.X>=0&&i.Cursor.Y>=0&&i.Cursor.X<i.Size.X&&i.Cursor.Y<i.Size.Y,
             "YC59_CONSOLE_POSITION_INVALID");
        metrics.buffer_width=i.Size.X;metrics.buffer_height=i.Size.Y;metrics.cursor_x=i.Cursor.X;metrics.cursor_y=i.Cursor.Y;
        return i;
    }
    static string Cells(int start,int length,int width) {
        Need(start>=0&&length>=0&&length<=131072&&width>0,"YC59_CONSOLE_READ_RANGE_INVALID");
        if(length==0) return "";
        // This API returns a character count, not a null-terminated string.
        // The guard character is never included in the decoded screen data.
        IntPtr buffer=Marshal.AllocHGlobal(checked((length+1)*2));
        try {
            Marshal.WriteInt16(buffer,length*2,0);
            metrics.stage="read.characters";metrics.requested_characters=(uint)length;
            uint n;bool ok=ReadConsoleOutputCharacter(output,buffer,(uint)length,new XY{X=(short)(start%width),Y=(short)(start/width)},out n);
            metrics.read_characters=n;Api(ok,"YC59_CONSOLE_READ_FAILED");
            Need(n==length,"YC59_CONSOLE_READ_INCOMPLETE");
            return Marshal.PtrToStringUni(buffer,(int)n);
        } finally {Marshal.FreeHGlobal(buffer);}
    }
    public static string Text() {
        Info i=ScreenInfo();
        int end=i.Cursor.Y*i.Size.X+i.Cursor.X;
        int start=Math.Max(0,end-60000); start-=start%i.Size.X;
        if(end==start) return "";
        return Normalize(Cells(start,end-start,i.Size.X),i.Size.X);
    }
    public static string Normalize(string raw,int width) {
        Need(width>0,"YC59_CONSOLE_WIDTH_INVALID");
        var result=new StringBuilder();
        for(int p=0;p<raw.Length;p+=width) {
            string row=raw.Substring(p,Math.Min(width,raw.Length-p));
            string trimmed=row.TrimEnd(' ');
            result.Append(trimmed);
            if(row.Length-trimmed.Length==1 && row.Length==width) result.Append(' ');
        }
        return result.ToString();
    }
    public static bool At(string prompt) {
        Need(prompt!=null&&prompt.Length>0&&prompt.Length<=8192,"YC59_CONSOLE_PROMPT_INVALID");
        metrics.expected_prompt_characters=prompt.Length;metrics.prompt_matched=false;metrics.prompt_snapshot_stable=false;
        Info before=ScreenInfo();int end=before.Cursor.Y*before.Size.X+before.Cursor.X;
        if(end<prompt.Length) return false;
        string tail=Cells(end-prompt.Length,prompt.Length,before.Size.X);
        Info after=ScreenInfo();
        metrics.prompt_snapshot_stable=before.Cursor.X==after.Cursor.X&&before.Cursor.Y==after.Cursor.Y&&before.Size.X==after.Size.X&&before.Size.Y==after.Size.Y;
        metrics.stage="prompt.compare";
        metrics.prompt_matched=metrics.prompt_snapshot_stable&&String.Equals(tail,prompt,StringComparison.Ordinal);
        return metrics.prompt_matched;
    }
    public static void Line(string expectedPrompt,string value) {
        metrics.stage="line.validation";metrics.win32_error=0;
        Need(value!=null && value.Length>0 && value.Length<=8192,"YC59_CONSOLE_VALUE_INVALID");
        foreach(char c in value) Need(!char.IsControl(c),"YC59_CONSOLE_CONTROL_REFUSED");
        bool at=At(expectedPrompt);metrics.stage="line.prompt";Need(at,"YC59_CONSOLE_PROMPT_CHANGED");
        // Refuse an already queued keyboard/mouse event; never overwrite human input.
        metrics.stage="line.queue-count";
        uint pending; Api(GetNumberOfConsoleInputEvents(input,out pending),"YC59_CONSOLE_INPUT_COUNT_FAILED");
        metrics.pending_before_write=pending;Need(pending<=128,"YC59_CONSOLE_INPUT_PENDING");
        if(pending>0) {
            var peek=new Input[pending]; uint count;
            metrics.stage="line.queue-inspect";Api(PeekConsoleInput(input,peek,pending,out count),"YC59_CONSOLE_INPUT_UNAVAILABLE");
            for(int k=0;k<count;k++) Need(peek[k].Type!=1||peek[k].Down==0,"YC59_CONSOLE_INPUT_PENDING");
        }
        var records=new Input[(value.Length+1)*2];
        try {
            for(int j=0;j<=value.Length;j++) {
                char c=j==value.Length?'\r':value[j];
                records[j*2]=new Input{Type=1,Down=1,Repeat=1,Character=c,VirtualKey=(ushort)(c=='\r'?13:0)};
                records[j*2+1]=new Input{Type=1,Down=0,Repeat=1,Character=c,VirtualKey=(ushort)(c=='\r'?13:0)};
            }
            metrics.stage="line.write";metrics.requested_records=(uint)records.Length;
            uint written;bool ok=WriteConsoleInput(input,records,(uint)records.Length,out written);metrics.written_records=written;
            Api(ok,"YC59_CONSOLE_WRITE_FAILED_NO_RETRY");Need(written==records.Length,"YC59_CONSOLE_WRITE_INCOMPLETE_NO_RETRY");
            metrics.stage="line.submitted";
        } finally { Array.Clear(records,0,records.Length); }
    }
    public static void Close() {
        if(input!=null) input.Dispose(); if(output!=null) output.Dispose(); input=null;output=null;FreeConsole();
    }
}
