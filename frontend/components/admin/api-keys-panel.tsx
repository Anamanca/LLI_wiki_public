"use client";

import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useApiKeys,
  useCreateApiKey,
  useUpdateApiKey,
  useDeleteApiKey,
  useActivateApiKey,
} from "@/hooks/use-api-keys";
import { Key, Plus, Trash2, Play, Pause, AlertCircle } from "lucide-react";
import type { ApiKeyRow } from "@/types";

function StatusBadge({ status, rateLimitedUntil }: { status: string; rateLimitedUntil: string | null }) {
  if (status === "active") {
    return <Badge variant="success">Active</Badge>;
  }
  if (status === "rate_limited") {
    if (rateLimitedUntil) {
      const remaining = Math.max(0, Math.round((new Date(rateLimitedUntil).getTime() - Date.now()) / 1000));
      const mins = Math.floor(remaining / 60);
      const secs = remaining % 60;
      const countdown = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
      return (
        <Badge variant="rate_limited" className="gap-1">
          <AlertCircle className="h-3 w-3" />
          Rate Limited ({countdown})
        </Badge>
      );
    }
    return <Badge variant="rate_limited">Rate Limited</Badge>;
  }
  return <Badge variant="secondary">Disabled</Badge>;
}

export function ApiKeysPanel() {
  const { data: keys, isLoading, error } = useApiKeys();
  const createKey = useCreateApiKey();
  const updateKey = useUpdateApiKey();
  const deleteKey = useDeleteApiKey();
  const activateKey = useActivateApiKey();

  const [showForm, setShowForm] = useState(false);
  const [provider, setProvider] = useState("opencode");
  const [apiKey, setApiKey] = useState("");
  const [modelName, setModelName] = useState("deepseek-v4-flash");
  const [priority, setPriority] = useState(0);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!apiKey.trim()) return;
    try {
      await createKey.mutateAsync({
        provider,
        api_key: apiKey.trim(),
        model_name: modelName || "deepseek-v4-flash",
        priority,
      });
      setApiKey("");
      setModelName("deepseek-v4-flash");
      setPriority(0);
      setShowForm(false);
    } catch (err) {
      console.error("Failed to create API key:", err);
    }
  }

  async function handleToggleStatus(key: ApiKeyRow) {
    const newStatus = key.status === "active" ? "disabled" : "active";
    try {
      await updateKey.mutateAsync({ id: key.id, payload: { status: newStatus } });
    } catch (err) {
      console.error("Failed to update API key:", err);
    }
  }

  async function handleActivate(keyId: string) {
    try {
      await activateKey.mutateAsync(keyId);
    } catch (err) {
      console.error("Failed to activate API key:", err);
    }
  }

  async function handleDelete(keyId: string) {
    if (!confirm("Permanently delete this API key?")) return;
    try {
      await deleteKey.mutateAsync(keyId);
    } catch (err) {
      console.error("Failed to delete API key:", err);
    }
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="flex items-center gap-2">
          <Key className="h-4 w-4" />
          API Keys
        </CardTitle>
        <Button size="sm" onClick={() => setShowForm(!showForm)} disabled={createKey.isPending}>
          <Plus className="mr-1 h-3 w-3" />
          {showForm ? "Cancel" : "Add Key"}
        </Button>
      </CardHeader>
      <CardContent>
        {/* Add Key Form */}
        {showForm && (
          <form onSubmit={handleCreate} className="mb-4 space-y-3 rounded-lg border p-4">
            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <label className="mb-1 block text-xs font-medium text-muted-foreground">Provider</label>
                <Select value={provider} onValueChange={setProvider}>
                  <SelectValue placeholder="Provider" />
                  <SelectContent>
                    <SelectItem value="opencode">OpenCode</SelectItem>
                    <SelectItem value="gemini">Gemini</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-muted-foreground">Model</label>
                <Input
                  value={modelName}
                  onChange={(e) => setModelName(e.target.value)}
                  placeholder="deepseek-v4-flash"
                />
              </div>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">API Key</label>
              <Input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="sk-..."
                required
              />
            </div>
            <div className="flex items-end gap-3">
              <div className="w-24">
                <label className="mb-1 block text-xs font-medium text-muted-foreground">Priority</label>
                <Input
                  type="number"
                  min={0}
                  max={100}
                  value={priority}
                  onChange={(e) => setPriority(Number(e.target.value))}
                />
              </div>
              <Button type="submit" size="sm" disabled={createKey.isPending || !apiKey.trim()}>
                {createKey.isPending ? "Adding..." : "Add Key"}
              </Button>
            </div>
            {createKey.isError && (
              <p className="text-xs text-red-500">{(createKey.error as Error)?.message || "Failed to create key"}</p>
            )}
          </form>
        )}

        {/* Keys Table */}
        {isLoading ? (
          <div className="space-y-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : error ? (
          <p className="text-sm text-red-500">Failed to load API keys.</p>
        ) : !keys || keys.length === 0 ? (
          <p className="text-sm text-muted-foreground">No API keys configured. Add one above or set keys in .env.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-muted-foreground">
                  <th className="pb-2 pr-3 font-medium">Provider</th>
                  <th className="pb-2 pr-3 font-medium">Key</th>
                  <th className="pb-2 pr-3 font-medium">Model</th>
                  <th className="pb-2 pr-3 font-medium">Status</th>
                  <th className="pb-2 pr-3 font-medium">Priority</th>
                  <th className="pb-2 pr-3 font-medium">Used</th>
                  <th className="pb-2 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {keys.map((key) => (
                  <tr key={key.id} className="border-b last:border-0">
                    <td className="py-2 pr-3 font-medium">{key.provider === "opencode" ? "OpenCode" : key.provider === "gemini" ? "Gemini" : key.provider}</td>
                    <td className="py-2 pr-3 font-mono text-xs">{key.api_key_masked}</td>
                    <td className="py-2 pr-3">{key.model_name}</td>
                    <td className="py-2 pr-3">
                      <StatusBadge status={key.status} rateLimitedUntil={key.rate_limited_until} />
                    </td>
                    <td className="py-2 pr-3">{key.priority}</td>
                    <td className="py-2 pr-3 text-muted-foreground">{key.usage_count}</td>
                    <td className="py-2">
                      <div className="flex items-center gap-1">
                        {key.status !== "active" && (
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7"
                            onClick={() => handleActivate(key.id)}
                            disabled={activateKey.isPending}
                            title="Activate"
                          >
                            <Play className="h-3 w-3 text-green-500" />
                          </Button>
                        )}
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          onClick={() => handleToggleStatus(key)}
                          disabled={updateKey.isPending}
                          title={key.status === "active" ? "Disable" : "Enable"}
                        >
                          <Pause className="h-3 w-3 text-orange-500" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          onClick={() => handleDelete(key.id)}
                          disabled={deleteKey.isPending}
                          title="Delete"
                        >
                          <Trash2 className="h-3 w-3 text-red-500" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}