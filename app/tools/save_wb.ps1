# save_wb.ps1 —— 只在 Mechanical 窗口内保存（Ctrl+S）
# 不碰 Workbench，不改变窗口大小/位置（保持最大化状态）
param([int]$WaitSec = 3)

$ErrorActionPreference = 'SilentlyContinue'

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class MechSave {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsZoomed(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool f);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, IntPtr p);
  [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
  public struct RECT { public int L,T,R,B; }
  public static IntPtr Find(uint target) {
    IntPtr found = IntPtr.Zero;
    EnumWindows((h,l) => {
      uint pid; GetWindowThreadProcessId(h, out pid);
      if (pid != target || !IsWindowVisible(h)) return true;
      RECT r; GetWindowRect(h, out r);
      if (r.R - r.L < 800) return true;
      found = h; return false;
    }, IntPtr.Zero);
    return found;
  }
  public static bool Activate(IntPtr h) {
    // 最小化时才恢复；已最大化则保持不变
    bool wasMax = IsZoomed(h);
    if (IsIconic(h)) { ShowWindow(h, 9); }      // SW_RESTORE
    keybd_event(0x12,0,0,UIntPtr.Zero); keybd_event(0x12,0,2,UIntPtr.Zero);
    System.Threading.Thread.Sleep(100);
    uint cur = GetCurrentThreadId();
    uint tgt = GetWindowThreadProcessId(h, IntPtr.Zero);
    AttachThreadInput(cur, tgt, true);
    bool ok = SetForegroundWindow(h);
    AttachThreadInput(cur, tgt, false);
    System.Threading.Thread.Sleep(300);
    if (wasMax) { ShowWindow(h, 3); }           // SW_MAXIMIZE 保持最大化
    return ok;
  }
  public static void CtrlS() {
    keybd_event(0x11, 0, 0, UIntPtr.Zero);
    System.Threading.Thread.Sleep(60);
    keybd_event(0x53, 0, 0, UIntPtr.Zero);
    System.Threading.Thread.Sleep(60);
    keybd_event(0x53, 0, 2, UIntPtr.Zero);
    System.Threading.Thread.Sleep(60);
    keybd_event(0x11, 0, 2, UIntPtr.Zero);
  }
  public static string Rect(IntPtr h) {
    RECT r; GetWindowRect(h, out r);
    return r.L + "," + r.T + "," + r.R + "," + r.B;
  }
}
"@

$proc = Get-Process -Name 'AnsysWBU' -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $proc) {
  Write-Output '{"ok":false,"error":"Mechanical 未运行"}'
  exit 0
}

$hwnd = [MechSave]::Find([uint32]$proc.Id)
if ($hwnd -eq [IntPtr]::Zero) {
  Write-Output '{"ok":false,"error":"找不到 Mechanical 窗口"}'
  exit 0
}

$before = [MechSave]::Rect($hwnd)
$ok = [MechSave]::Activate($hwnd)
Start-Sleep -Milliseconds 300
[MechSave]::CtrlS()
Start-Sleep -Seconds $WaitSec
$after = [MechSave]::Rect($hwnd)

Write-Output ('{"ok":true,"target":"Mechanical","pid":' + $proc.Id +
  ',"activated":' + $ok.ToString().ToLower() +
  ',"rect_before":"' + $before + '","rect_after":"' + $after + '"}')
