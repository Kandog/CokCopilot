// Bridge for the local "connect to server" page only. Pages loaded from the
// server get nothing, so a server can't change which server the app uses.
import { contextBridge, ipcRenderer } from "electron";

if (location.protocol === "file:") {
  contextBridge.exposeInMainWorld("cokDesktop", {
    getServer: (): Promise<string> => ipcRenderer.invoke("cok:get-server"),
    connect: (url: string): Promise<{ ok: boolean; error?: string }> => ipcRenderer.invoke("cok:connect", url),
  });
}
