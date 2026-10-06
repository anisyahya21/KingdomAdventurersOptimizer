import { useEffect, useMemo, useState } from "react";

import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { apiUrl } from "@/lib/api";

const LOCAL_DEVICE_ID_KEY = "kaSyncCurrentDeviceId";
const LOCAL_DEVICE_NAME_KEY = "kaSyncCurrentDeviceName";

type Device = {
	id: string;
	name: string;
	createdAt: number;
};

type ApiError = {
	error?: string;
};


async function readJson<T>(res: Response): Promise<T> {
	const text = await res.text();
	return text ? (JSON.parse(text) as T) : ({} as T);
}

function readLocalDeviceId() {
	if (typeof window === "undefined") return "";
	return localStorage.getItem(LOCAL_DEVICE_ID_KEY) || "";
}

function readLocalDeviceName() {
	if (typeof window === "undefined") return "";
	return localStorage.getItem(LOCAL_DEVICE_NAME_KEY) || "";
}

export default function SyncDevicesPage() {
	const [code, setCode] = useState("");
	const [expiresAt, setExpiresAt] = useState<number | null>(null);
	const [copied, setCopied] = useState(false);

	const [showEnter, setShowEnter] = useState(false);
	const [enteredCode, setEnteredCode] = useState("");

	const [deviceName, setDeviceName] = useState(() => readLocalDeviceName());
	const [currentDeviceId, setCurrentDeviceId] = useState(() => readLocalDeviceId());

	const [devices, setDevices] = useState<Device[]>([]);

	const [isLoadingDevices, setIsLoadingDevices] = useState(false);
	const [isGenerating, setIsGenerating] = useState(false);
	const [isRedeeming, setIsRedeeming] = useState(false);
	const [removingId, setRemovingId] = useState<string | null>(null);

	const [message, setMessage] = useState("");
	const [error, setError] = useState("");

	const [now, setNow] = useState(Date.now());

	const trimmedDeviceName = deviceName.trim();
	const currentDevice = devices.find((device) => device.id === currentDeviceId) ?? null;
	const isCurrentDeviceLinked = !!currentDevice;
	const resolvedDeviceName = currentDevice?.name || trimmedDeviceName;

	useEffect(() => {
		localStorage.setItem(LOCAL_DEVICE_NAME_KEY, deviceName);
	}, [deviceName]);

	useEffect(() => {
		if (!currentDeviceId) {
			localStorage.removeItem(LOCAL_DEVICE_ID_KEY);
			return;
		}

		localStorage.setItem(LOCAL_DEVICE_ID_KEY, currentDeviceId);
	}, [currentDeviceId]);

	useEffect(() => {
		if (currentDevice?.name && currentDevice.name !== deviceName) {
			setDeviceName(currentDevice.name);
		}

		if (currentDevice && showEnter) {
			setShowEnter(false);
			setEnteredCode("");
		}
	}, [currentDevice, deviceName, showEnter]);

	async function loadDevices(deviceId = currentDeviceId) {
		setIsLoadingDevices(true);

		try {
			const url = new URL(apiUrl("/sync/devices"), window.location.origin);
			if (deviceId) {
				url.searchParams.set("currentDeviceId", deviceId);
			}

			const res = await fetch(url.toString());
			const data = await readJson<Device[] | ApiError>(res);

			if (!res.ok) {
				throw new Error((data as ApiError).error || "Failed to load devices.");
			}

			const nextDevices = Array.isArray(data) ? data : [];
			setDevices(nextDevices);
		} catch (err) {
			setError(err instanceof Error ? err.message : "Failed to load devices.");
		} finally {
			setIsLoadingDevices(false);
		}
	}

	useEffect(() => {
		loadDevices();
	}, [currentDeviceId]);

	useEffect(() => {
		if (!expiresAt) return;

		const interval = window.setInterval(() => {
			const current = Date.now();
			setNow(current);

			if (current >= expiresAt) {
				setCode("");
				setExpiresAt(null);
				setCopied(false);
			}
		}, 1000);

		return () => window.clearInterval(interval);
	}, [expiresAt]);

	const countdown = useMemo(() => {
		if (!expiresAt) return "";
		const diff = Math.max(0, expiresAt - now);
		const total = Math.floor(diff / 1000);
		const minutes = Math.floor(total / 60);
		const seconds = total % 60;
		return `Expires in ${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
	}, [expiresAt, now]);

	function requireDeviceName() {
		if (resolvedDeviceName.trim()) return true;
		setError("Choose a name for this device first.");
		setMessage("");
		return false;
	}

	async function generateCode() {
		if (!requireDeviceName()) return;

		setIsGenerating(true);
		setError("");
		setMessage("");

		try {
			const res = await fetch(apiUrl("/sync/generate"), {
				method: "POST",
				headers: {
					"Content-Type": "application/json"
				},
				body: JSON.stringify({
					name: resolvedDeviceName,
					currentDeviceId: currentDeviceId || null
				})
			});

			const data = await readJson<{
				ok?: boolean;
				code?: string;
				expiresAt?: number;
				currentDeviceId?: string;
				device?: Device;
				error?: string;
			}>(res);

			if (!res.ok || !data.code || !data.expiresAt) {
				throw new Error(data.error || "Failed to generate code.");
			}

			const nextCurrentDeviceId = data.currentDeviceId || currentDeviceId;

			if (data.currentDeviceId) {
				setCurrentDeviceId(data.currentDeviceId);
			}

			if (data.device?.name) {
				setDeviceName(data.device.name);
			}

			setCode(data.code);
			setExpiresAt(data.expiresAt);
			setShowEnter(false);
			setCopied(false);
			setNow(Date.now());
			setMessage("Sync code generated.");

			await loadDevices(nextCurrentDeviceId);
		} catch (err) {
			setError(err instanceof Error ? err.message : "Failed to generate code.");
		} finally {
			setIsGenerating(false);
		}
	}

	async function copyCode() {
		if (!code) return;

		try {
			await navigator.clipboard.writeText(code);
			setCopied(true);
			setMessage("Code copied.");
			window.setTimeout(() => setCopied(false), 1200);
		} catch {
			setError("Could not copy the code.");
		}
	}

	function openEnterCode() {
		if (!requireDeviceName()) return;
		setError("");
		setMessage("");
		setShowEnter(true);
	}

	async function redeemCode() {
		if (!requireDeviceName()) return;

		const normalizedCode = enteredCode.trim().toUpperCase();

		if (!normalizedCode) {
			setError("Please enter a code.");
			setMessage("");
			return;
		}

		setIsRedeeming(true);
		setError("");
		setMessage("");

		try {
			const res = await fetch(apiUrl("/sync/redeem"), {
				method: "POST",
				headers: {
					"Content-Type": "application/json"
				},
				body: JSON.stringify({
					code: normalizedCode,
					name: resolvedDeviceName,
					currentDeviceId: currentDeviceId || null
				})
			});

			const data = await readJson<{
				ok?: boolean;
				message?: string;
				currentDeviceId?: string;
				device?: Device;
				error?: string;
			}>(res);

			if (!res.ok) {
				throw new Error(data.error || "Failed to link device.");
			}

			const nextCurrentDeviceId = data.currentDeviceId || currentDeviceId;

			if (data.currentDeviceId) {
				setCurrentDeviceId(data.currentDeviceId);
			}

			if (data.device?.name) {
				setDeviceName(data.device.name);
			}

			setEnteredCode("");
			setShowEnter(false);
			setCode("");
			setExpiresAt(null);
			setCopied(false);
			setMessage(data.message || "Device linked successfully.");

			await loadDevices(nextCurrentDeviceId);
		} catch (err) {
			setError(err instanceof Error ? err.message : "Failed to link device.");
		} finally {
			setIsRedeeming(false);
		}
	}

	async function removeDevice(id: string) {
		if (!currentDeviceId) {
			setError("This device is not linked yet.");
			setMessage("");
			return;
		}

		setRemovingId(id);
		setError("");
		setMessage("");

		try {
			const url = new URL(apiUrl(`/sync/device/${id}`), window.location.origin);
			url.searchParams.set("currentDeviceId", currentDeviceId);

			const res = await fetch(url.toString(), {
				method: "DELETE"
			});

			const data = await readJson<{
				ok?: boolean;
				error?: string;
			}>(res);

			if (!res.ok) {
				throw new Error(data.error || "Failed to remove device.");
			}

			const nextCurrentDeviceId = id === currentDeviceId ? "" : currentDeviceId;

			if (id === currentDeviceId) {
				setCurrentDeviceId("");
				localStorage.removeItem(LOCAL_DEVICE_ID_KEY);
			}

			setMessage("Device removed.");
			await loadDevices(nextCurrentDeviceId);
		} catch (err) {
			setError(err instanceof Error ? err.message : "Failed to remove device.");
		} finally {
			setRemovingId(null);
		}
	}

	return (
		<div className="max-w-4xl mx-auto px-4 py-6 space-y-6">
			<div className="flex items-center justify-between gap-4">
				<div>
					<h1 className="text-2xl font-bold">Sync Devices</h1>
					<p className="text-sm opacity-80 mt-2">
						Sync your saved data across your phone, tablet, and computer without creating an account.
					</p>
				</div>

</div>

			{(message || error) && (
				<Card>
					<CardContent className="p-4">
						{message && <p className="text-sm">{message}</p>}
						{error && <p className="text-sm text-red-500 mt-1">{error}</p>}
					</CardContent>
				</Card>
			)}

			<Card>
				<CardContent className="p-4 space-y-4">
					<h2 className="text-lg font-semibold">Sync with a Code</h2>

					<p className="text-sm opacity-80">
						Generate a one-time code to sync your saved data across your devices.
					</p>

					<div className="space-y-2">
						<label className="text-sm font-medium">This device name</label>
						<Input
							placeholder="Enter a name for this device"
							value={deviceName}
							onChange={(e) => setDeviceName(e.target.value)}
							disabled={isGenerating || isRedeeming}
						/>
					</div>

					{code && (
						<div className="space-y-2">
							<div className="flex gap-2 flex-wrap items-center">
								<div className="border rounded-md px-3 py-2 font-mono tracking-wide">
									{code}
								</div>

								<Button
									variant="outline"
									onClick={copyCode}
									disabled={!code}
								>
									{copied ? "Copied" : "Copy"}
								</Button>
							</div>

							<p className="text-xs opacity-70">{countdown}</p>
						</div>
					)}

					{showEnter && (
						<div className="space-y-2">
							<Input
								placeholder="Enter code"
								value={enteredCode}
								onChange={(e) => setEnteredCode(e.target.value.toUpperCase())}
								disabled={isRedeeming}
							/>

							<div className="flex gap-2 flex-wrap">
								<Button onClick={redeemCode} disabled={isRedeeming}>
									{isRedeeming ? "Linking..." : "Link Device"}
								</Button>

								<Button
									variant="outline"
									onClick={() => {
										setShowEnter(false);
										setEnteredCode("");
										setError("");
									}}
									disabled={isRedeeming}
								>
									Cancel
								</Button>
							</div>
						</div>
					)}

					<div className="flex gap-3 flex-wrap items-center">
						<Button onClick={generateCode} disabled={isGenerating}>
							{isGenerating ? "Generating..." : "Generate Code"}
						</Button>

						{!isCurrentDeviceLinked ? (
							<Button
								variant="outline"
								onClick={openEnterCode}
								disabled={isRedeeming}
							>
								Enter Code
							</Button>
						) : (
							<p className="text-sm opacity-70">This device is already linked.</p>
						)}
					</div>
				</CardContent>
			</Card>

			<Card>
				<CardContent className="p-4 space-y-4">
					<h2 className="text-lg font-semibold">Synced Devices</h2>

					{isLoadingDevices ? (
						<p className="text-sm opacity-70">Loading devices...</p>
					) : (
						<div className="space-y-3">
							{devices.map((device) => {
								const isCurrent = device.id === currentDeviceId;

								return (
									<div
										key={device.id}
										className="flex items-center justify-between gap-3 border rounded-md px-3 py-3"
									>
										<div className="min-w-0">
											<div className="font-medium break-words">
												{device.name}
												{isCurrent ? " (This device)" : ""}
											</div>
											<div className="text-xs opacity-70">
												Linked {new Date(device.createdAt).toLocaleString()}
											</div>
										</div>

										<Button
											variant="outline"
											onClick={() => removeDevice(device.id)}
											disabled={removingId === device.id}
										>
											{removingId === device.id ? "Removing..." : "Remove"}
										</Button>
									</div>
								);
							})}

							{devices.length === 0 && (
								<p className="text-sm opacity-70">
									No synced devices yet.
								</p>
							)}
						</div>
					)}
				</CardContent>
			</Card>
		</div>
	);
}
