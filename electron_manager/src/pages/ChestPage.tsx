import { useEffect, useRef, useState } from "react";
import { Camera, CircleStop, Download, FolderOpen, Play, Plus, RefreshCw, Settings2, Tag, Trash2, Upload, UserRound, X } from "lucide-react";
import { LogActions, LogPanel, PageHeading } from "../components/layout";
import type { SharedProps } from "../app/shared";

const chestDeviceStorageKey = "chest-selected-device";
const chestRealtimeLogsStorageKey = "chest-show-realtime-logs";
const chestUserStorageKey = "chest-selected-user";
const chestSourceStoragePrefix = "chest-selected-source:";
const chestRunModeStorageKey = "chest-run-mode";

type ChestDay = { day: string; count: number; latestAt: string };
type ChestItem = { item_id?: string; item_name?: string; quantity?: number | null; crop_path?: string; icon_crop_path?: string };
type ChestItemCorrection = { slot: number; itemName?: string | null; itemId?: string | null; iconCropPath?: string | null; quantity: number | null };
type ChestItemSummary = { itemId: string; itemName: string; sourceId?: string; sourceName?: string; cropPath?: string; iconCropPath?: string; totalQuantity: number; itemCount: number; unreadQuantityCount: number; dropProbability: number; expectedQuantity: number | null };
type CatalogItem = { itemId: string; name: string; labeled: boolean; weight?: number | null; cropPath: string; occurrences: number };
type ChestUser = { id: string; name: string; createdAt: string };
type ChestSource = { id: string; name: string };
type CustomChestSource = { sourceId: string; sourceName: string };
type SummaryRange = "day" | "7d" | "month" | "custom";
type ReanalyzeScope = "day" | "all";
type ChestRunMode = "normal" | "skip_magnifier" | "capture_current";
const builtInChestSources: ChestSource[] = [{ id: "boss_jinjia", name: "金甲" }, { id: "boss_dayan", name: "大眼" }];
const chestSources: ChestSource[] = [...builtInChestSources, { id: "custom", name: "自定义" }];

function defaultChestSource(userName: string): ChestSource {
  return userName === "熊大" ? chestSources[1] : chestSources[0];
}

function storageScope(taskId: string | undefined) {
  return (taskId || "main").replace(/[^a-zA-Z0-9_-]/g, "_");
}

function scopedStorageKey(key: string, scope: string) {
  return `${key}:${scope}`;
}

function sourceStorageKey(userId: string, scope: string) {
  return `${chestSourceStoragePrefix}${scope}:${userId}`;
}

function sortSummaryByWeight(items: ChestItemSummary[]): ChestItemSummary[] {
  return [...items].sort((left, right) => {
    const leftRawWeight = (left as ChestItemSummary & { weight?: unknown }).weight;
    const rightRawWeight = (right as ChestItemSummary & { weight?: unknown }).weight;
    const leftWeight = Number(leftRawWeight);
    const rightWeight = Number(rightRawWeight);
    const leftHasWeight = leftRawWeight !== null && leftRawWeight !== undefined && String(leftRawWeight).trim() !== "" && Number.isFinite(leftWeight);
    const rightHasWeight = rightRawWeight !== null && rightRawWeight !== undefined && String(rightRawWeight).trim() !== "" && Number.isFinite(rightWeight);
    if (leftHasWeight !== rightHasWeight) return leftHasWeight ? -1 : 1;
    if (leftHasWeight && leftWeight !== rightWeight) return leftWeight - rightWeight;
    return Number(right.totalQuantity) - Number(left.totalQuantity);
  });
}

function sortCatalogItems(items: CatalogItem[]): CatalogItem[] {
  return [...items].sort((left, right) => {
    const leftWeight = Number(left.weight);
    const rightWeight = Number(right.weight);
    const leftHasWeight = left.weight !== null && left.weight !== undefined && Number.isFinite(leftWeight);
    const rightHasWeight = right.weight !== null && right.weight !== undefined && Number.isFinite(rightWeight);
    const leftGroup = leftHasWeight ? 0 : !left.labeled ? 1 : 2;
    const rightGroup = rightHasWeight ? 0 : !right.labeled ? 1 : 2;
    if (leftGroup !== rightGroup) return leftGroup - rightGroup;
    if (leftHasWeight && rightHasWeight && leftWeight !== rightWeight) return leftWeight - rightWeight;
    return left.itemId.localeCompare(right.itemId);
  });
}

export function ChestPage(props: SharedProps) {
  const storageKeyScope = storageScope(props.chestTaskId);
  const deviceStorageKey = scopedStorageKey(chestDeviceStorageKey, storageKeyScope);
  const userStorageKey = scopedStorageKey(chestUserStorageKey, storageKeyScope);
  const realtimeLogsStorageKey = scopedStorageKey(chestRealtimeLogsStorageKey, storageKeyScope);
  const runModeStorageKey = scopedStorageKey(chestRunModeStorageKey, storageKeyScope);
  const [device, setDevice] = useState(() => window.localStorage.getItem(deviceStorageKey) ?? window.localStorage.getItem(chestDeviceStorageKey) ?? "");
  const [userId, setUserId] = useState(() => props.chestUserId ?? window.localStorage.getItem(userStorageKey) ?? window.localStorage.getItem(chestUserStorageKey) ?? "default");
  const [users, setUsers] = useState<ChestUser[]>([]);
  const [showUserModal, setShowUserModal] = useState(false);
  const [newUserName, setNewUserName] = useState("");
  const [userNameDrafts, setUserNameDrafts] = useState<Record<string, string>>({});
  const [days, setDays] = useState<ChestDay[]>([]);
  const [screenshots, setScreenshots] = useState<Array<Record<string, unknown>>>([]);
  const [editingScreenshots, setEditingScreenshots] = useState(false);
  const [itemEvents, setItemEvents] = useState<Array<Record<string, unknown>>>([]);
  const [itemSummary, setItemSummary] = useState<ChestItemSummary[]>([]);
  const [itemSummaryImages, setItemSummaryImages] = useState<Record<string, string | null>>({});
  const [summaryBoxCount, setSummaryBoxCount] = useState(0);
  const [summaryRange, setSummaryRange] = useState<SummaryRange>("day");
  const [summarySourceId, setSummarySourceId] = useState("");
  const [customStartDay, setCustomStartDay] = useState("");
  const [customEndDay, setCustomEndDay] = useState("");
  const [showExportModal, setShowExportModal] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exportUserId, setExportUserId] = useState(userId);
  const [exportRange, setExportRange] = useState<SummaryRange>("day");
  const [exportStartDay, setExportStartDay] = useState("");
  const [exportEndDay, setExportEndDay] = useState("");
  const [exportSourceId, setExportSourceId] = useState("");
  const [exportSources, setExportSources] = useState<CustomChestSource[]>([]);
  const [refreshingHistory, setRefreshingHistory] = useState(false);
  const [reanalyzing, setReanalyzing] = useState(false);
  const [showReanalyzeModal, setShowReanalyzeModal] = useState(false);
  const [reanalyzeScope, setReanalyzeScope] = useState<ReanalyzeScope>("day");
  const [reanalyzeUserId, setReanalyzeUserId] = useState(userId);
  const [sourceId, setSourceId] = useState(() => {
    const stored = window.localStorage.getItem(sourceStorageKey(userId, storageKeyScope)) || window.localStorage.getItem(sourceStorageKey(userId, "main"));
    return props.chestSourceId?.startsWith("custom_") ? props.chestSourceId : props.chestSourceId || (stored === "custom" ? "boss_jinjia" : stored) || "boss_jinjia";
  });
  const [customSourceName, setCustomSourceName] = useState(() => props.chestSourceId?.startsWith("custom_") ? props.chestSourceName ?? "" : "");
  const [customSources, setCustomSources] = useState<CustomChestSource[]>([]);
  const [newCustomSourceName, setNewCustomSourceName] = useState("");
  const [showSourceManager, setShowSourceManager] = useState(false);
  const [showLabelModal, setShowLabelModal] = useState(false);
  const [unlabeledItems, setUnlabeledItems] = useState<CatalogItem[]>([]);
  const [labelImages, setLabelImages] = useState<Record<string, string | null>>({});
  const [labelDrafts, setLabelDrafts] = useState<Record<string, string>>({});
  const [weightDrafts, setWeightDrafts] = useState<Record<string, string>>({});
  const [savingItemId, setSavingItemId] = useState("");
  const [calibrationEvent, setCalibrationEvent] = useState<Record<string, unknown> | null>(null);
  const [calibrationItems, setCalibrationItems] = useState<ChestItem[]>([]);
  const [calibrationCatalogItems, setCalibrationCatalogItems] = useState<CatalogItem[]>([]);
  const [calibrationImages, setCalibrationImages] = useState<Record<number, string | null>>({});
  const [calibrationNameDrafts, setCalibrationNameDrafts] = useState<Record<number, string>>({});
  const [calibrationDrafts, setCalibrationDrafts] = useState<Record<number, string>>({});
  const [calibrationUserId, setCalibrationUserId] = useState("default");
  const [calibrationSourceId, setCalibrationSourceId] = useState("boss_jinjia");
  const [calibrationCustomSourceName, setCalibrationCustomSourceName] = useState("");
  const [calibrationSources, setCalibrationSources] = useState<CustomChestSource[]>([]);
  const [savingCalibration, setSavingCalibration] = useState(false);
  const [selectedDay, setSelectedDay] = useState("");
  const selectedDayRef = useRef("");
  const historyRefreshingRef = useRef(false);
  const historyRefreshQueuedRef = useRef(false);
  const historyRequestRef = useRef(0);
  const [images, setImages] = useState<Record<string, string | null>>({});
  const [showRealtimeLogs, setShowRealtimeLogs] = useState(() => window.localStorage.getItem(realtimeLogsStorageKey) === "true" || window.localStorage.getItem(chestRealtimeLogsStorageKey) === "true");
  const [runMode, setRunMode] = useState<ChestRunMode>(() => {
    const stored = window.localStorage.getItem(runModeStorageKey);
    if (stored === "normal" || stored === "skip_magnifier" || stored === "capture_current") return stored;
    return window.localStorage.getItem(scopedStorageKey("chest-skip-magnifier", storageKeyScope)) === "true" ? "skip_magnifier" : "normal";
  });
  const taskId = props.chestTaskId ?? "chest";
  const running = !!props.running[taskId];
  const rawLog = props.logs[taskId] ?? props.logs.chest ?? "";
  const log = showRealtimeLogs
    ? rawLog
    : rawLog.split(/\r?\n/).filter((line) => !/if_image \[\d+\/\d+\] template .+ not matched|CMD adb shell input tap/.test(line)).join("\n");
  const plan = props.plans.includes("开宝箱截图.json") ? "开宝箱截图.json" : "";

  const loadUsers = async () => {
    const nextUsers = await window.bsManager.chestUsers();
    setUsers(nextUsers);
    setUserNameDrafts(Object.fromEntries(nextUsers.map((user) => [user.id, user.name])));
    if (!nextUsers.some((user) => user.id === userId)) setUserId("default");
  };

  useEffect(() => {
    const current = users.find((user) => user.id === userId);
    const stored = window.localStorage.getItem(sourceStorageKey(userId, storageKeyScope))
      || window.localStorage.getItem(sourceStorageKey(userId, "main"));
    const storedCustomName = window.localStorage.getItem(`${sourceStorageKey(userId, storageKeyScope)}:name`)
      || window.localStorage.getItem(`${sourceStorageKey(userId, "main")}:name`)
      || "";
    const legacyCustom = stored === "custom" && storedCustomName
      ? customSources.find((source) => source.sourceName === storedCustomName)
      : null;
    const initial = props.chestSourceId?.startsWith("custom_")
      ? props.chestSourceId
      : props.chestSourceId || legacyCustom?.sourceId || (stored === "custom" ? defaultChestSource(current?.name ?? "").id : stored) || defaultChestSource(current?.name ?? "").id;
    setSourceId(initial);
    const selectedCustom = customSources.find((source) => source.sourceId === initial);
    setCustomSourceName(props.chestSourceId?.startsWith("custom_") ? props.chestSourceName ?? "" : selectedCustom?.sourceName ?? "");
  }, [userId, users.length, customSources, props.chestSourceId, props.chestSourceName]);

  useEffect(() => {
    void window.bsManager.chestSources(userId)
      .then((sources) => setCustomSources(sources))
      .catch((error) => props.setNotice(`读取自定义来源失败: ${String(error)}`));
  }, [userId]);

  useEffect(() => {
    setDevice((current) => {
      const remembered = current || window.localStorage.getItem(deviceStorageKey) || window.localStorage.getItem(chestDeviceStorageKey) || "";
      const nextDevice = props.devices.includes(remembered) ? remembered : (props.devices[0] ?? "");
      if (nextDevice) window.localStorage.setItem(deviceStorageKey, nextDevice);
      else window.localStorage.removeItem(deviceStorageKey);
      return nextDevice;
    });
  }, [props.devices]);

  const selectDevice = (nextDevice: string) => {
    setDevice(nextDevice);
    if (nextDevice) window.localStorage.setItem(deviceStorageKey, nextDevice);
    else window.localStorage.removeItem(deviceStorageKey);
  };

  const selectUser = (nextUserId: string) => {
    setUserId(nextUserId);
    setSummarySourceId("");
    window.localStorage.setItem(userStorageKey, nextUserId);
    setShowUserModal(false);
  };

  const openUserManager = () => {
    if (props.license?.tier !== "pro") {
      props.setNotice("多用户开宝箱需要专业版，请在设置中输入激活码");
      return;
    }
    void loadUsers().then(() => setShowUserModal(true)).catch((error) => props.setNotice(`读取开箱用户失败: ${String(error)}`));
  };

  const createUser = () => {
    const name = newUserName.trim();
    if (!name) return;
    void window.bsManager.chestCreateUser(name).then(async (user) => {
      setNewUserName("");
      await loadUsers();
      selectUser(user.id);
    }).catch((error) => props.setNotice(`新增用户失败: ${String(error)}`));
  };

  const renameUser = (user: ChestUser) => {
    const name = (userNameDrafts[user.id] ?? "").trim();
    if (!name || name === user.name) return;
    void window.bsManager.chestRenameUser(user.id, name).then(() => loadUsers()).catch((error) => props.setNotice(`重命名用户失败: ${String(error)}`));
  };

  const updateRealtimeLogs = (next: boolean) => {
    setShowRealtimeLogs(next);
    window.localStorage.setItem(realtimeLogsStorageKey, String(next));
  };

  const loadDay = async (day: string) => {
    const requestId = ++historyRequestRef.current;
    if (!day) {
      setScreenshots([]);
      setItemEvents([]);
      setItemSummary([]);
      setSummaryBoxCount(0);
      setImages({});
      return;
    }
    const [records, events] = await Promise.all([
      window.bsManager.chestScreenshots(day, "", userId),
      window.bsManager.chestItemEvents(day, "", userId),
    ]);
    if (requestId !== historyRequestRef.current) return;
    const existingPaths = new Set(records.map((record) => String(record.before_path ?? "")));
    const syncedRecords = events
      .filter((event) => !existingPaths.has(String(event.screenshot_path ?? "")))
      .map((event) => ({ before_path: String(event.screenshot_path ?? ""), before_saved_at: String(event.captured_at ?? ""), source_name: event.source_name, remote_only: true }));
    const displayRecords = [...records, ...syncedRecords].sort((left, right) => String(right.before_saved_at ?? "").localeCompare(String(left.before_saved_at ?? "")));
    setScreenshots(displayRecords);
    setItemEvents(events);
    const nextImages = await Promise.all(displayRecords.map(async (record) => {
      const file = String(record.before_path ?? "");
      return [file, file && !record.remote_only ? await window.bsManager.chestImage(file) : null] as const;
    }));
    if (requestId !== historyRequestRef.current) return;
    setImages(Object.fromEntries(nextImages));
  };

  const refreshSummary = async (day = selectedDay, latestDay = days[0]?.day) => {
    const summaryEndDay = summaryRange === "custom"
      ? customEndDay
      : summaryRange === "day"
        ? day
        : latestDay || day;
    if (!summaryEndDay) {
      setItemSummary([]);
      setSummaryBoxCount(0);
      return;
    }
    const rangeStartDay = summaryRange === "custom" ? customStartDay : undefined;
    if (summaryRange === "custom" && (!rangeStartDay || rangeStartDay > summaryEndDay)) {
      setItemSummary([]);
      setSummaryBoxCount(0);
      return;
    }
    const summary = await window.bsManager.chestSummaryRange(summaryEndDay, summaryRange, "", userId, rangeStartDay, summarySourceId);
    const nextItems = sortSummaryByWeight(summary.items as ChestItemSummary[]);
    setItemSummary(nextItems);
    const imageEntries = await Promise.all(nextItems.map(async (item) => [
      `${item.sourceId ?? ""}:${item.itemId}:${item.itemName}`,
      item.iconCropPath ? await window.bsManager.chestImage(item.iconCropPath) : null,
    ] as const));
    setItemSummaryImages(Object.fromEntries(imageEntries));
    setSummaryBoxCount(summary.boxCount);
  };

  const exportReport = () => {
    const endDay = exportRange === "custom"
      ? exportEndDay
      : exportRange === "day"
        ? exportEndDay
        : days[0]?.day || selectedDay;
    const startDay = exportRange === "custom" ? exportStartDay : undefined;
    if (!endDay) return;
    if (exportRange === "custom" && (!startDay || startDay > endDay)) {
      props.setNotice("请选择有效的自定义日期范围");
      return;
    }
    setExporting(true);
    void window.bsManager.chestExportReport(endDay, exportRange, "", exportUserId, startDay, exportSourceId)
      .then((result) => {
        setShowExportModal(false);
        props.setNotice(`报表已导出：${result.file}`);
        if (window.confirm("报表已导出，是否打开报表目录？")) {
          void window.bsManager.chestOpenReportDirectory()
            .catch((error) => props.setNotice(`打开报表目录失败: ${String(error)}`));
        }
      })
      .catch((error) => props.setNotice(`导出报表失败: ${String(error)}`))
      .finally(() => setExporting(false));
  };

  const openExportModal = () => {
    const defaultEndDay = summaryRange === "custom"
      ? customEndDay
      : summaryRange === "day"
        ? selectedDay
        : days[0]?.day || selectedDay;
    setExportUserId(userId);
    setExportRange(summaryRange);
    setExportStartDay(summaryRange === "custom" ? customStartDay : defaultEndDay);
    setExportEndDay(defaultEndDay);
    setExportSourceId(summarySourceId);
    setShowExportModal(true);
    void window.bsManager.chestSources(userId)
      .then((sources) => setExportSources(sources))
      .catch((error) => props.setNotice(`读取报表来源失败: ${String(error)}`));
  };

  const updateExportUser = (nextUserId: string) => {
    setExportUserId(nextUserId);
    void window.bsManager.chestSources(nextUserId)
      .then((sources) => {
        setExportSources(sources);
        setExportSourceId((current) => (
          current === "" || builtInChestSources.some((source) => source.id === current) || sources.some((source) => source.sourceId === current)
            ? current
            : ""
        ));
      })
      .catch((error) => props.setNotice(`读取报表来源失败: ${String(error)}`));
  };

  const selectExportRange = (nextRange: SummaryRange) => {
    const latestDay = days[0]?.day || selectedDay;
    setExportRange(nextRange);
    if (nextRange === "custom") {
      setExportStartDay((current) => current || exportEndDay || latestDay);
      setExportEndDay((current) => current || latestDay);
    } else if (nextRange === "day") {
      setExportEndDay(selectedDay);
    } else {
      setExportEndDay(latestDay);
    }
  };

  const exportSyncData = () => {
    void window.bsManager.chestSyncExport(userId)
      .then((result) => {
        if (!result.canceled) props.setNotice(`已导出 ${result.events} 条开箱记录和 ${result.icons} 个物品图标`);
      })
      .catch((error) => props.setNotice(`导出同步数据失败: ${String(error)}`));
  };

  const importSyncData = () => {
    void window.bsManager.chestSyncImport(userId)
      .then(async (result) => {
        if (result.canceled) return;
        await refreshHistory(true);
        props.setNotice(`已导入 ${result.imported} 条记录，跳过 ${result.skipped} 条重复记录，新增 ${result.icons} 个图标`);
      })
      .catch((error) => props.setNotice(`导入同步数据失败: ${String(error)}`));
  };

  const refreshHistory = async (force = false) => {
    if (historyRefreshingRef.current) {
      if (force) historyRefreshQueuedRef.current = true;
      return;
    }
    historyRefreshingRef.current = true;
    setRefreshingHistory(true);
    try {
      const nextDays = await window.bsManager.chestListDays("", userId);
      setDays(nextDays);
      const nextDay = nextDays.some((item) => item.day === selectedDayRef.current)
        ? selectedDayRef.current
        : nextDays[0]?.day ?? "";
      selectedDayRef.current = nextDay;
      setSelectedDay(nextDay);
      await loadDay(nextDay);
      await refreshSummary(nextDay, nextDays[0]?.day);
    } catch (error) {
      props.setNotice(`读取开宝箱截图失败: ${String(error)}`);
    } finally {
      historyRefreshingRef.current = false;
      setRefreshingHistory(false);
      if (historyRefreshQueuedRef.current) {
        historyRefreshQueuedRef.current = false;
        void refreshHistory();
      }
    }
  };

  useEffect(() => { void loadUsers(); }, []);
  useEffect(() => { void refreshHistory(); }, [userId]);
  useEffect(() => { void refreshSummary().catch((error) => props.setNotice(String(error))); }, [selectedDay, summaryRange, customStartDay, customEndDay, summarySourceId, userId]);
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => void refreshHistory(), 10_000);
    return () => window.clearInterval(timer);
  }, [running]);
  useEffect(() => {
    const refreshAfterExit = () => void refreshHistory();
    window.addEventListener("chest-task-finished", refreshAfterExit);
    return () => window.removeEventListener("chest-task-finished", refreshAfterExit);
  }, []);

  const selectedSource = (): ChestSource | null => {
    if (sourceId === "custom") {
      const name = customSourceName.trim();
      if (!name) return null;
      return { id: `custom_${name.replace(/[^a-zA-Z0-9\u4e00-\u9fff]+/g, "_")}`, name };
    }
    const builtIn = chestSources.find((source) => source.id === sourceId);
    if (builtIn) return builtIn;
    const custom = customSources.find((source) => source.sourceId === sourceId);
    if (custom) return { id: custom.sourceId, name: custom.sourceName };
    return sourceId.startsWith("custom_") && customSourceName.trim()
      ? { id: sourceId, name: customSourceName.trim() }
      : null;
  };

  const saveSourceSelection = (source: ChestSource) => {
    window.localStorage.setItem(sourceStorageKey(userId, storageKeyScope), source.id);
    window.localStorage.setItem(`${sourceStorageKey(userId, storageKeyScope)}:name`, source.name);
  };

  const selectSource = (nextId: string) => {
    setSourceId(nextId);
    const custom = customSources.find((source) => source.sourceId === nextId);
    setCustomSourceName(custom?.sourceName ?? "");
  };

  const createCustomSource = () => {
    const name = newCustomSourceName.trim();
    if (!name) return;
    void window.bsManager.chestCreateSource(userId, name)
      .then((source) => {
        setCustomSources((current) => current.some((item) => item.sourceId === source.sourceId) ? current : [...current, source]);
        setSourceId(source.sourceId);
        setCustomSourceName(source.sourceName);
        setNewCustomSourceName("");
        saveSourceSelection({ id: source.sourceId, name: source.sourceName });
        props.setNotice(`已新增自定义来源：${source.sourceName}`);
      })
      .catch((error) => props.setNotice(`新增自定义来源失败: ${String(error)}`));
  };

  const removeCustomSource = (source: CustomChestSource) => {
    if (!window.confirm(`删除自定义来源“${source.sourceName}”？历史记录不会删除。`)) return;
    void window.bsManager.chestDeleteSource(userId, source.sourceId)
      .then(() => {
        setCustomSources((current) => current.filter((item) => item.sourceId !== source.sourceId));
        if (sourceId === source.sourceId) {
          setSourceId("boss_jinjia");
          setCustomSourceName("");
          saveSourceSelection({ id: "boss_jinjia", name: "金甲" });
        }
        if (summarySourceId === source.sourceId) setSummarySourceId("");
      })
      .catch((error) => props.setNotice(`删除自定义来源失败: ${String(error)}`));
  };

  const syncActiveSource = async () => {
    let source = selectedSource();
    if (!source) {
      props.setNotice("请填写宝箱来源");
      return null;
    }
    if (source.id.startsWith("custom_")) {
      const saved = await window.bsManager.chestCreateSource(userId, source.name);
      source = { id: saved.sourceId, name: saved.sourceName };
      setCustomSources((current) => current.some((item) => item.sourceId === saved.sourceId) ? current : [...current, saved]);
      setSourceId(saved.sourceId);
      setCustomSourceName(saved.sourceName);
    }
    const result = await window.bsManager.chestSetActiveSource(userId, taskId, source.id, source.name);
    saveSourceSelection(source);
    return result;
  };

  const start = async () => {
    if (!props.runtime || !plan) {
      props.setNotice("找不到 开宝箱截图.json 计划");
      return;
    }
    try {
      const source = selectedSource();
      if (!source) {
        props.setNotice("请填写宝箱来源");
        return;
      }
      const activeSource = await syncActiveSource();
      if (!activeSource) return;
      const args = [`${props.runtime.root}/adb_bot.py`, "--plan", `${props.runtime.plansDir}/${plan}`, "--adb", props.settings.adbPath];
      if (device) args.push("--device", device);
      args.push("--user-id", userId, "--source-id", source.id, "--source-name", source.name, "--source-file", activeSource.sourceFile);
      if (runMode === "skip_magnifier") args.push("--skip-magnifier");
      if (runMode === "capture_current") args.push("--capture-current-chest");
      void refreshHistory();
      await props.startTask(taskId, args);
    } catch (error) {
      props.setNotice(`无法设置宝箱来源: ${String(error)}`);
    }
  };

  const selectDay = (day: string) => {
    selectedDayRef.current = day;
    setSelectedDay(day);
    void loadDay(day);
  };

  const openReanalyze = () => {
    setReanalyzeScope(selectedDay ? "day" : "all");
    setReanalyzeUserId(userId);
    setShowReanalyzeModal(true);
  };

  const reanalyze = () => {
    if (reanalyzing) return;
    const day = reanalyzeScope === "day" ? selectedDay : undefined;
    if (reanalyzeScope === "day" && !day) {
      props.setNotice("请先选择要重新识别的日期");
      return;
    }
    setReanalyzing(true);
    const selectedUser = users.find((user) => user.id === reanalyzeUserId) ?? currentUser;
    void window.bsManager.chestReanalyze(day, reanalyzeUserId)
      .then(async (result) => {
        await refreshHistory();
        setShowReanalyzeModal(false);
        props.setNotice(`已为 ${selectedUser.name} 重新识别 ${Number(result.analyzed ?? 0)} 张截图`);
      })
      .catch((error) => props.setNotice(`物品识别失败: ${String(error)}`))
      .finally(() => setReanalyzing(false));
  };

  const openLabelManager = () => {
    void window.bsManager.chestUnlabeledItems().then(async (items) => {
      const sortedItems = sortCatalogItems(items);
      setUnlabeledItems(sortedItems);
      setLabelDrafts(Object.fromEntries(items.map((item) => [item.itemId, item.labeled ? item.name : ""])));
      setWeightDrafts(Object.fromEntries(items.map((item) => [item.itemId, item.weight == null ? "" : String(item.weight)])));
      setShowLabelModal(true);
      const images = await Promise.all(sortedItems.map(async (item) => [item.itemId, item.cropPath ? await window.bsManager.chestImage(item.cropPath) : null] as const));
      setLabelImages(Object.fromEntries(images));
    }).catch((error) => props.setNotice(`读取待标注物品失败: ${String(error)}`));
  };

  const saveLabel = (item: CatalogItem) => {
    const name = (labelDrafts[item.itemId] ?? "").trim();
    if (!name || savingItemId) return;
    const rawWeight = (weightDrafts[item.itemId] ?? "").trim();
    const weight = rawWeight === "" ? null : Number(rawWeight);
    if (weight !== null && (!Number.isFinite(weight) || weight < 0)) {
      props.setNotice("权重必须是非负数字");
      return;
    }
    setSavingItemId(item.itemId);
    void window.bsManager.chestLabelItem(item.itemId, name).then(async (result) => {
      await window.bsManager.chestSetItemWeight(String(result.itemId ?? item.itemId), weight);
      await refreshHistory(true);
      openLabelManager();
    }).catch((error) => props.setNotice(`保存物品标注失败: ${String(error)}`)).finally(() => setSavingItemId(""));
  };

  const deleteCatalogItem = (item: CatalogItem) => {
    if (!window.confirm(`删除物品图鉴“${item.name}”？历史开箱记录不会删除。`)) return;
    void window.bsManager.chestDeleteItem(item.itemId).then(() => openLabelManager()).catch((error) => props.setNotice(`删除物品失败: ${String(error)}`));
  };

  const deleteScreenshot = (file: string) => {
    if (!window.confirm("删除这次开箱截图及其识别记录？此操作不可撤销。")) return;
    void window.bsManager.chestDeleteEvent(file).then(() => refreshHistory()).catch((error) => props.setNotice(`删除截图失败: ${String(error)}`));
  };

  const openCalibration = (record: Record<string, unknown>, event: Record<string, unknown> | undefined) => {
    const items = (event?.items ?? []) as ChestItem[];
    const recordUserId = String(record.user_id ?? event?.user_id ?? "default");
    const recordSourceId = String(record.source_id ?? event?.source_id ?? "");
    const recordSourceName = String(record.source_name ?? event?.source_name ?? "");
    setCalibrationEvent(record);
    setCalibrationItems(items);
    setCalibrationUserId(recordUserId);
    setCalibrationSourceId(recordSourceId || "custom");
    setCalibrationCustomSourceName(recordSourceName);
    setCalibrationNameDrafts(Object.fromEntries(items.map((item, index) => [index + 1, item.item_name && item.item_name !== "待标注物品" ? item.item_name : ""])));
    setCalibrationDrafts(Object.fromEntries(items.map((item, index) => [index + 1, item.quantity == null ? "" : String(item.quantity)])));
    setCalibrationCatalogItems([]);
    void window.bsManager.chestUnlabeledItems()
      .then((catalogItems) => setCalibrationCatalogItems(catalogItems.filter((item) => item.labeled)))
      .catch((error) => props.setNotice(`读取已标记物品失败: ${String(error)}`));
    void window.bsManager.chestSources(recordUserId)
      .then((sources) => {
        const existingRecordSource = recordSourceId.startsWith("custom_") && recordSourceName
          ? { sourceId: recordSourceId, sourceName: recordSourceName }
          : null;
        setCalibrationSources(existingRecordSource && !sources.some((source) => source.sourceId === existingRecordSource.sourceId)
          ? [...sources, existingRecordSource]
          : sources);
      })
      .catch((error) => props.setNotice(`读取校准来源失败: ${String(error)}`));
    void Promise.all(items.map(async (item, index) => [index + 1, item.crop_path ? await window.bsManager.chestImage(item.crop_path) : null] as const))
      .then((images) => setCalibrationImages(Object.fromEntries(images)))
      .catch((error) => props.setNotice(`读取校准图片失败: ${String(error)}`));
  };

  const addCalibrationItem = () => {
    setCalibrationItems((current) => [...current, { item_name: "", quantity: null }]);
  };

  const selectCalibrationItem = (slot: number, value: string) => {
    if (value === "__custom__") {
      setCalibrationNameDrafts((current) => ({ ...current, [slot]: "" }));
      setCalibrationImages((current) => ({ ...current, [slot]: null }));
      return;
    }
    const catalogItem = calibrationCatalogItems.find((item) => item.name === value);
    setCalibrationNameDrafts((current) => ({ ...current, [slot]: value }));
    if (catalogItem?.cropPath) {
      void window.bsManager.chestImage(catalogItem.cropPath)
        .then((image) => setCalibrationImages((current) => ({ ...current, [slot]: image })))
        .catch((error) => props.setNotice(`读取物品图标失败: ${String(error)}`));
    }
  };

  const removeCalibrationItem = (slot: number) => {
    setCalibrationItems((current) => current.filter((_, index) => index + 1 !== slot));
    setCalibrationNameDrafts((current) => Object.fromEntries(Object.entries(current)
      .filter(([key]) => Number(key) !== slot)
      .map(([key, value]) => [Number(key) > slot ? String(Number(key) - 1) : key, value])));
    setCalibrationDrafts((current) => Object.fromEntries(Object.entries(current)
      .filter(([key]) => Number(key) !== slot)
      .map(([key, value]) => [Number(key) > slot ? String(Number(key) - 1) : key, value])));
    setCalibrationImages((current) => Object.fromEntries(Object.entries(current)
      .filter(([key]) => Number(key) !== slot)
      .map(([key, value]) => [Number(key) > slot ? String(Number(key) - 1) : key, value])));
  };

  const selectCalibrationUser = (nextUserId: string) => {
    setCalibrationUserId(nextUserId);
    void window.bsManager.chestSources(nextUserId)
      .then((sources) => {
        setCalibrationSources(sources);
        setCalibrationSourceId((current) => (
          builtInChestSources.some((source) => source.id === current) || sources.some((source) => source.sourceId === current)
            ? current
            : "custom"
        ));
      })
      .catch((error) => props.setNotice(`读取校准来源失败: ${String(error)}`));
  };

  const selectCalibrationSource = (nextSourceId: string) => {
    setCalibrationSourceId(nextSourceId);
    const saved = calibrationSources.find((source) => source.sourceId === nextSourceId);
    setCalibrationCustomSourceName(saved?.sourceName ?? "");
  };

  const saveCalibration = () => {
    if (!calibrationEvent || savingCalibration) return;
    const screenshotPath = String(calibrationEvent.before_path ?? "");
    if (!screenshotPath) {
      props.setNotice("未找到本次开箱截图，无法保存校准");
      return;
    }
    if (!users.some((user) => user.id === calibrationUserId)) {
      props.setNotice("请选择有效用户");
      return;
    }
    const corrections: ChestItemCorrection[] = calibrationItems.map((item, index) => {
      const slot = index + 1;
      const raw = (calibrationDrafts[index + 1] ?? "").trim();
      const itemName = (calibrationNameDrafts[index + 1] ?? item.item_name ?? "").trim();
      const catalogItem = calibrationCatalogItems.find((candidate) => candidate.name === itemName);
      return {
        slot,
        itemName: itemName || null,
        itemId: catalogItem?.itemId ?? item.item_id ?? null,
        iconCropPath: catalogItem?.cropPath ?? item.icon_crop_path ?? item.crop_path ?? null,
        quantity: raw === "" ? null : Number(raw),
      };
    });
    if (corrections.some((item) => !item.itemName)) {
      props.setNotice("请为每个校准物品选择名称或填写自定义名称");
      return;
    }
    if (corrections.some((item) => item.quantity !== null && (!Number.isInteger(item.quantity) || item.quantity < 0))) {
      props.setNotice("数量必须是非负整数");
      return;
    }
    setSavingCalibration(true);
    void (async () => {
      const builtIn = builtInChestSources.find((source) => source.id === calibrationSourceId);
      const saved = calibrationSources.find((source) => source.sourceId === calibrationSourceId);
      let source: { sourceId: string; sourceName: string };
      if (builtIn) {
        source = { sourceId: builtIn.id, sourceName: builtIn.name };
      } else if (saved) {
        source = saved;
      } else if (calibrationSourceId === "custom") {
        const customName = calibrationCustomSourceName.trim();
        if (!customName) throw new Error("请填写宝箱来源");
        source = await window.bsManager.chestCreateSource(calibrationUserId, customName);
      } else if (calibrationSourceId.startsWith("custom_") && calibrationCustomSourceName.trim()) {
        source = await window.bsManager.chestCreateSource(calibrationUserId, calibrationCustomSourceName.trim());
      } else {
        throw new Error("来源已失效，请重新选择宝箱来源");
      }
      if (!source?.sourceId || !source.sourceName) throw new Error("请填写宝箱来源");
      setCalibrationSources((current) => current.some((item) => item.sourceId === source.sourceId) ? current : [...current, source]);
      if (calibrationUserId === userId) setCustomSources((current) => current.some((item) => item.sourceId === source.sourceId) ? current : [...current, source]);
      await window.bsManager.chestCorrectEvent(screenshotPath, corrections, {
        userId: calibrationUserId,
        sourceId: source.sourceId,
        sourceName: source.sourceName,
      });
      await refreshHistory();
      setCalibrationEvent(null);
    })()
      .catch((error) => props.setNotice(`保存物品校准失败: ${String(error)}`))
      .finally(() => setSavingCalibration(false));
  };

  const currentUser = users.find((user) => user.id === userId) ?? { id: "default", name: "默认用户", createdAt: "" };
  const currentSource = selectedSource();
  const manualRefreshHistory = () => {
    void refreshHistory().then(() => props.setNotice("开宝箱记录已刷新"));
  };
  const updateRunMode = (nextMode: ChestRunMode) => {
    setRunMode(nextMode);
    window.localStorage.setItem(runModeStorageKey, nextMode);
  };
  const activateSummaryRange = (nextRange: SummaryRange) => {
    if (nextRange === "custom") {
      setCustomStartDay((current) => current || selectedDay);
      setCustomEndDay((current) => current || selectedDay);
    }
    setSummaryRange(nextRange);
  };
  const summaryTitle = summaryRange === "day" ? "当日" : summaryRange === "7d" ? "最近 7 天" : summaryRange === "month" ? "最近 30 天" : "自定义范围";
  const summaryDetail = summaryRange === "day" ? selectedDay || "暂无日期" : summaryRange === "7d" ? "最近 7 天" : summaryRange === "month" ? "最近 30 天" : customStartDay && customEndDay ? `${customStartDay} 至 ${customEndDay}` : "请选择日期";
  const customRangeInvalid = summaryRange === "custom" && (!customStartDay || !customEndDay || customStartDay > customEndDay);
  const startLabel = runMode === "capture_current" ? "保存当前物品" : runMode === "skip_magnifier" ? "执行一次开箱" : "开始开宝箱";
  return <div className="page">
    <PageHeading title="开宝箱控制台" detail="保存每次开箱后的物品列表截图，并按天归档回看。"><div className="chest-heading-actions"><button className="button secondary" onClick={openUserManager}><UserRound size={16} />切换用户：{currentUser.name}</button><button className="button secondary" onClick={() => void props.refreshDevices()}><RefreshCw size={16} />刷新设备</button><button className="button secondary" onClick={() => void window.bsManager.openChestWindow(plan, userId, currentSource?.id, currentSource?.name)}><Play size={16} />多开开宝箱</button><button className="button secondary" onClick={() => void window.bsManager.chestOpenScreenshots()}><FolderOpen size={16} />打开截图</button><button className="button secondary" onClick={openLabelManager}><Tag size={16} />物品标注</button><button className="button secondary" onClick={exportSyncData}><Download size={16} />导出同步数据</button><button className="button secondary" onClick={importSyncData}><Upload size={16} />导入同步数据</button><button className="button secondary" disabled={reanalyzing} onClick={openReanalyze}><RefreshCw className={reanalyzing ? "spin" : ""} size={16} />{reanalyzing ? "正在识别" : "重新识别"}</button><button className="button secondary" disabled={reanalyzing || refreshingHistory} onClick={manualRefreshHistory}><RefreshCw className={refreshingHistory ? "spin" : ""} size={16} />{refreshingHistory ? "刷新中" : "刷新记录"}</button></div></PageHeading>
    <div className="chest-console-layout">
      <section className="panel form-panel">
        <label>计划<input readOnly value={plan || "未找到 开宝箱截图.json"} /></label>
        <label>设备<select value={device} onChange={(event) => selectDevice(event.target.value)}>{props.devices.map((name) => <option key={name}>{name}</option>)}</select></label>
        <div className="chest-source-field"><label>来源<select value={sourceId} onChange={(event) => selectSource(event.target.value)}>{builtInChestSources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}{customSources.map((source) => <option key={source.sourceId} value={source.sourceId}>{source.sourceName}</option>)}</select></label><button className="icon-button" title="管理来源" onClick={() => setShowSourceManager(true)}><Settings2 size={16} /></button></div>
        {!running ? <button className="button primary full" disabled={!plan || !currentSource} onClick={() => void start()}>{runMode === "capture_current" ? <Camera size={16} /> : <Play size={16} />}{startLabel}</button> : <><button className="button secondary full" disabled={!currentSource} onClick={() => void syncActiveSource().then((source) => { if (source) props.setNotice(`后续宝箱来源已切换为 ${source.sourceName}`); }).catch((error) => props.setNotice(`切换来源失败: ${String(error)}`))}>切换来源</button><button className="button danger full" onClick={() => void window.bsManager.stopTask(taskId)}><CircleStop size={16} />停止开宝箱</button></>}
        <div className="chest-run-mode"><span>执行方式</span><div className="segmented-control" role="tablist" aria-label="开宝箱执行方式"><button type="button" role="tab" aria-selected={runMode === "normal"} className={runMode === "normal" ? "active" : ""} onClick={() => updateRunMode("normal")}>正常开箱</button><button type="button" role="tab" aria-selected={runMode === "skip_magnifier"} className={runMode === "skip_magnifier" ? "active" : ""} onClick={() => updateRunMode("skip_magnifier")}>跳过放大镜</button><button type="button" role="tab" aria-selected={runMode === "capture_current"} className={runMode === "capture_current" ? "active" : ""} onClick={() => updateRunMode("capture_current")}>保存当前物品</button></div></div>
        <LogPanel title="开宝箱日志" text={log} actions={<LogActions text={rawLog} showRealtimeLogs={showRealtimeLogs} onToggleRealtimeLogs={updateRealtimeLogs} onClear={() => props.clearTaskLog(taskId)} />} />
      </section>
      <section className="chest-workspace">
        <section className="panel chest-current-status">
          <div className="panel-title"><span>当前开箱状态</span><span className={`run-state ${running ? "live" : ""}`}>{running ? "运行中，自动刷新" : "已停止"}</span></div>
          <div className="metric-grid"><Metric label="当前用户" value={currentUser.name} /><Metric label="当前来源" value={currentSource?.name ?? "未设置"} /><Metric label="统计宝箱数" value={summaryBoxCount} detail={summaryDetail} /></div>
        </section>
        <section className="panel chest-current-status">
          <div className="panel-title"><span>{summaryTitle}物品统计</span><div className="chest-summary-actions"><div className="segmented-control" role="tablist" aria-label="统计范围"><button className={summaryRange === "day" ? "active" : ""} onClick={() => activateSummaryRange("day")}>当日</button><button className={summaryRange === "7d" ? "active" : ""} onClick={() => activateSummaryRange("7d")}>最近7天</button><button className={summaryRange === "month" ? "active" : ""} onClick={() => activateSummaryRange("month")}>最近30天</button><button className={summaryRange === "custom" ? "active" : ""} onClick={() => activateSummaryRange("custom")}>自定义</button></div><select className="chest-summary-source-select" aria-label="统计来源" value={summarySourceId} onChange={(event) => setSummarySourceId(event.target.value)}><option value="">全部来源</option>{builtInChestSources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}{customSources.map((source) => <option key={source.sourceId} value={source.sourceId}>{source.sourceName}</option>)}</select><button className="button secondary" disabled={!selectedDay || customRangeInvalid} onClick={openExportModal}>导出报表</button><span className="counter">{summaryBoxCount} 箱</span></div></div>
          {summaryRange === "custom" ? <div className="chest-custom-range"><label>开始日期<input type="date" value={customStartDay} max={customEndDay || undefined} onChange={(event) => setCustomStartDay(event.target.value)} /></label><span>至</span><label>结束日期<input type="date" value={customEndDay} min={customStartDay || undefined} onChange={(event) => setCustomEndDay(event.target.value)} /></label>{customRangeInvalid ? <small>请选择有效的日期范围</small> : null}</div> : null}
          {itemSummary.length ? <div className="chest-summary-table-wrap"><table className="chest-summary-table"><thead><tr><th>来源</th><th>物品</th><th>累计数量</th><th>掉落次数</th><th>掉落概率</th><th>期望/次开箱</th></tr></thead><tbody>{itemSummary.map((item) => { const imageKey = `${item.sourceId ?? ""}:${item.itemId}:${item.itemName}`; const image = itemSummaryImages[imageKey]; return <tr key={`${item.sourceId ?? ""}-${item.itemId}`}><td>{item.sourceName ?? "未分类"}</td><td className="item-name"><span className="chest-summary-item">{image ? <img src={image} alt="" /> : <span className="chest-summary-item-placeholder" aria-hidden="true" />}</span>{"\u00a0\u00a0"}<span>{item.itemName}</span></td><td>{item.totalQuantity || "待识别"}</td><td>{item.itemCount}</td><td>{item.dropProbability}%</td><td>{item.expectedQuantity == null ? "待识别" : item.expectedQuantity}</td></tr>; })}</tbody></table></div> : <p className="empty-note padded-note">截图识别完成后将显示当天物品和数量统计。</p>}
        </section>
        <div className="chest-history-layout">
          <section className="panel chest-day-list"><div className="panel-title"><span>按天统计</span><span className="counter">{days.length}</span></div><div className="draw-list-scroll">{days.length ? days.map((item) => <button key={item.day} className={`draw-list-item ${item.day === selectedDay ? "selected" : ""}`} onClick={() => selectDay(item.day)}><strong>{item.day}</strong><span>开箱记录 {item.count}</span><small>最近 {item.latestAt}</small></button>) : <p className="empty-note padded-note">尚无开箱记录。</p>}</div></section>
          <section className="panel chest-screenshot-list"><div className="panel-title"><span>{selectedDay ? `${selectedDay} 的物品列表` : "物品列表"}</span><span className="counter">{screenshots.length}</span><button className="button secondary" onClick={() => setEditingScreenshots((current) => !current)}>{editingScreenshots ? "完成" : "编辑"}</button></div>{screenshots.length ? <div className="chest-screenshot-grid">{screenshots.map((record) => { const file = String(record.before_path ?? ""); const savedAt = String(record.before_saved_at ?? ""); const remoteOnly = Boolean(record.remote_only); const event = itemEvents.find((entry) => String(entry.screenshot_path ?? "") === file); const items = (event?.items ?? []) as ChestItem[]; return <figure key={file} className="chest-screenshot">{remoteOnly ? <div className="chest-screenshot-placeholder">未同步原始截图</div> : <img src={images[file] ?? ""} alt={`开箱物品列表 ${savedAt}`} />}<figcaption><strong>{savedAt}</strong><span>{String(record.source_name ?? event?.source_name ?? "未分类")}</span><span>{items.length ? items.map((item) => `${item.item_name ?? "待标注"} x${item.quantity ?? "?"}`).join("，") : "等待物品识别"}</span><div className="chest-card-actions">{remoteOnly ? null : <button className="button secondary" onClick={() => openCalibration(record, event)}>校准</button>}{editingScreenshots && !remoteOnly ? <button className="icon-button danger" title="删除截图" onClick={() => deleteScreenshot(file)}><Trash2 size={15} /></button> : null}</div></figcaption></figure>; })}</div> : <p className="empty-note padded-note">选择有记录的日期查看每次开箱后的物品列表。</p>}</section>
        </div>
      </section>
    </div>
    {showUserModal ? <div className="chest-label-backdrop" role="dialog" aria-modal="true" aria-labelledby="chest-user-title"><section className="chest-label-modal chest-user-modal"><header><div><strong id="chest-user-title">切换用户</strong><span>截图和开箱记录按用户分别保存，物品图鉴共用。</span></div><button className="icon-button" title="关闭" onClick={() => setShowUserModal(false)}><X size={17} /></button></header><div className="chest-label-list">{users.map((user) => <div key={user.id} className={`chest-user-row ${user.id === userId ? "selected" : ""}`}><button className="button secondary" onClick={() => selectUser(user.id)}>{user.id === userId ? "当前用户" : "切换"}</button><input autoComplete="off" value={userNameDrafts[user.id] ?? ""} onChange={(event) => setUserNameDrafts((current) => ({ ...current, [user.id]: event.target.value }))} /><button className="button secondary" disabled={!userNameDrafts[user.id]?.trim() || userNameDrafts[user.id] === user.name} onClick={() => renameUser(user)}>重命名</button></div>)}</div><footer><div className="chest-user-create"><input autoComplete="off" placeholder="新用户名" value={newUserName} onChange={(event) => setNewUserName(event.target.value)} /><button className="button primary" disabled={!newUserName.trim()} onClick={createUser}>新增用户</button></div><button className="button secondary" onClick={() => setShowUserModal(false)}>关闭</button></footer></section></div> : null}
    {showSourceManager ? <div className="chest-label-backdrop" role="dialog" aria-modal="true" aria-labelledby="chest-source-title"><section className="chest-label-modal chest-source-modal"><header><div><strong id="chest-source-title">管理来源</strong><span>新增的来源会出现在来源下拉框中，删除不会影响历史记录。</span></div><button className="icon-button" title="关闭" onClick={() => setShowSourceManager(false)}><X size={17} /></button></header><div className="chest-label-list"><div className="chest-source-create"><input autoComplete="off" placeholder="来源名称" value={newCustomSourceName} onChange={(event) => setNewCustomSourceName(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") createCustomSource(); }} /><button className="button primary" disabled={!newCustomSourceName.trim()} onClick={createCustomSource}>新增来源</button></div>{customSources.length ? customSources.map((source) => <div key={source.sourceId} className={`chest-source-row ${sourceId === source.sourceId ? "selected" : ""}`}><span>{source.sourceName}</span><button className="icon-button danger" title={`删除 ${source.sourceName}`} onClick={() => removeCustomSource(source)}><Trash2 size={15} /></button></div>) : <p className="empty-note padded-note">暂无自定义来源。</p>}</div><footer><span>内置来源不可删除</span><button className="button secondary" onClick={() => setShowSourceManager(false)}>关闭</button></footer></section></div> : null}
    {showLabelModal ? <div className="chest-label-backdrop" role="dialog" aria-modal="true" aria-labelledby="chest-label-title"><section className="chest-label-modal"><header><div><strong id="chest-label-title">物品标注</strong><span>已设置权重的物品优先显示，其次是待标注物品。</span></div><button className="icon-button" title="关闭" onClick={() => setShowLabelModal(false)}><X size={17} /></button></header><div className="chest-label-list">{unlabeledItems.length ? unlabeledItems.map((item) => <div key={item.itemId} className={`chest-label-row ${item.labeled ? "labeled" : ""}`}><img src={labelImages[item.itemId] ?? ""} alt={item.name} /><div><code>{item.itemId}</code><small>{item.labeled ? `已标注：${item.name}` : "待标注"}，出现 {item.occurrences} 次</small></div><input autoComplete="off" placeholder="物品名称" value={labelDrafts[item.itemId] ?? ""} onChange={(event) => setLabelDrafts((current) => ({ ...current, [item.itemId]: event.target.value }))} /><input inputMode="decimal" placeholder="权重" value={weightDrafts[item.itemId] ?? ""} onChange={(event) => setWeightDrafts((current) => ({ ...current, [item.itemId]: event.target.value.replace(/[^0-9.]/g, "") }))} /><button className="button primary" disabled={!labelDrafts[item.itemId]?.trim() || !!savingItemId} onClick={() => saveLabel(item)}>{savingItemId === item.itemId ? "保存中" : "保存"}</button><button className="icon-button danger" title="删除物品图鉴" onClick={() => deleteCatalogItem(item)}><Trash2 size={15} /></button></div>) : <p className="empty-note padded-note">尚无已识别的物品。</p>}</div><footer><span>{unlabeledItems.filter((item) => !item.labeled).length} 个待标注，{unlabeledItems.length} 个物品</span><button className="button secondary" onClick={() => setShowLabelModal(false)}>关闭</button></footer></section></div> : null}
    {showReanalyzeModal ? <div className="chest-label-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !reanalyzing) setShowReanalyzeModal(false); }}><section className="chest-label-modal chest-reanalyze-modal" role="dialog" aria-modal="true" aria-labelledby="chest-reanalyze-title"><header><div><strong id="chest-reanalyze-title">重新识别</strong></div><button className="icon-button" title="关闭" disabled={reanalyzing} onClick={() => setShowReanalyzeModal(false)}><X size={17} /></button></header><div className="chest-reanalyze-options"><div className="segmented-control" role="group" aria-label="重新识别范围"><button className={reanalyzeScope === "day" ? "active" : ""} disabled={!selectedDay} onClick={() => setReanalyzeScope("day")}>{selectedDay ? `当前日期 ${selectedDay}` : "当前日期"}</button><button className={reanalyzeScope === "all" ? "active" : ""} onClick={() => setReanalyzeScope("all")}>全部记录</button></div>{users.length > 1 ? <label className="chest-reanalyze-user">用户<select value={reanalyzeUserId} disabled={reanalyzing} onChange={(event) => setReanalyzeUserId(event.target.value)}>{users.map((user) => <option key={user.id} value={user.id}>{user.name}</option>)}</select></label> : null}</div><footer><button className="button secondary" disabled={reanalyzing} onClick={() => setShowReanalyzeModal(false)}>取消</button><button className="button primary" disabled={reanalyzing || (reanalyzeScope === "day" && !selectedDay)} onClick={reanalyze}><RefreshCw className={reanalyzing ? "spin" : ""} size={16} />{reanalyzing ? "正在识别" : "开始识别"}</button></footer></section></div> : null}
    {showExportModal ? <div className="chest-label-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !exporting) setShowExportModal(false); }}><section className="chest-label-modal chest-export-modal" role="dialog" aria-modal="true" aria-labelledby="chest-export-title"><header><div><strong id="chest-export-title">导出报表</strong><span>选择用户、统计范围和来源后生成 CSV 报表。</span></div><button className="icon-button" title="关闭" disabled={exporting} onClick={() => setShowExportModal(false)}><X size={17} /></button></header><div className="chest-export-options"><label>用户<select value={exportUserId} disabled={exporting} onChange={(event) => updateExportUser(event.target.value)}>{users.map((user) => <option key={user.id} value={user.id}>{user.name}</option>)}</select></label><div className="chest-export-range"><span>统计范围</span><div className="segmented-control" role="group" aria-label="导出统计范围"><button className={exportRange === "day" ? "active" : ""} disabled={exporting} onClick={() => selectExportRange("day")}>当天</button><button className={exportRange === "7d" ? "active" : ""} disabled={exporting} onClick={() => selectExportRange("7d")}>最近7天</button><button className={exportRange === "month" ? "active" : ""} disabled={exporting} onClick={() => selectExportRange("month")}>最近30天</button><button className={exportRange === "custom" ? "active" : ""} disabled={exporting} onClick={() => selectExportRange("custom")}>自定义</button></div></div>{exportRange === "custom" ? <div className="chest-export-dates"><label>开始日期<input type="date" value={exportStartDay} disabled={exporting} max={exportEndDay || undefined} onChange={(event) => setExportStartDay(event.target.value)} /></label><span>至</span><label>结束日期<input type="date" value={exportEndDay} disabled={exporting} min={exportStartDay || undefined} onChange={(event) => setExportEndDay(event.target.value)} /></label></div> : null}<label>Boss 来源<select value={exportSourceId} disabled={exporting} onChange={(event) => setExportSourceId(event.target.value)}><option value="">全部来源</option>{builtInChestSources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}{exportSources.map((source) => <option key={source.sourceId} value={source.sourceId}>{source.sourceName}</option>)}</select></label>{exportRange === "day" ? <p className="field-note">统计日期：{exportEndDay || "暂无日期"}</p> : exportRange !== "custom" ? <p className="field-note">统计结束日期：{days[0]?.day || selectedDay || "暂无日期"}</p> : null}</div><footer><button className="button secondary" disabled={exporting} onClick={() => setShowExportModal(false)}>取消</button><button className="button primary" disabled={exporting || !exportEndDay || (exportRange === "custom" && (!exportStartDay || exportStartDay > exportEndDay))} onClick={exportReport}><Download size={16} />{exporting ? "导出中" : "开始导出"}</button></footer></section></div> : null}
    {calibrationEvent ? <div className="chest-label-backdrop" role="dialog" aria-modal="true" aria-labelledby="chest-calibration-title"><section className="chest-label-modal chest-calibration-modal"><header><div><strong id="chest-calibration-title">开箱记录校准</strong><span>{String(calibrationEvent.before_saved_at ?? "")}，可修正归属、物品和数量。</span></div><button className="icon-button" title="关闭" onClick={() => setCalibrationEvent(null)}><X size={17} /></button></header><div className="chest-calibration-body"><div className="chest-calibration-metadata"><label>用户<select value={calibrationUserId} onChange={(event) => selectCalibrationUser(event.target.value)}>{users.map((user) => <option key={user.id} value={user.id}>{user.name}</option>)}</select></label><label>Boss 来源<select value={calibrationSourceId} onChange={(event) => selectCalibrationSource(event.target.value)}>{builtInChestSources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}{calibrationSources.map((source) => <option key={source.sourceId} value={source.sourceId}>{source.sourceName}</option>)}<option value="custom">自定义</option></select></label>{calibrationSourceId === "custom" ? <label>自定义来源<input autoComplete="off" value={calibrationCustomSourceName} placeholder="Boss 名称" onChange={(event) => setCalibrationCustomSourceName(event.target.value)} /></label> : null}</div><div className="chest-label-list">{calibrationItems.length ? calibrationItems.map((item, index) => { const slot = index + 1; const draft = calibrationNameDrafts[slot] ?? ""; const labeled = calibrationCatalogItems.some((catalogItem) => catalogItem.name === draft); return <div key={slot} className="chest-label-row chest-calibration-row"><img src={calibrationImages[slot] ?? ""} alt={draft || "物品"} /><div><strong>{draft || "待命名物品"}</strong><small>第 {slot} 个物品</small></div><select value={labeled ? draft : "__custom__"} onChange={(input) => selectCalibrationItem(slot, input.target.value)}><option value="__custom__">自定义名称</option>{calibrationCatalogItems.map((catalogItem) => <option key={catalogItem.itemId} value={catalogItem.name}>{catalogItem.name}</option>)}</select>{!labeled ? <input autoComplete="off" value={draft} placeholder="输入物品名称" onChange={(input) => { setCalibrationNameDrafts((current) => ({ ...current, [slot]: input.target.value })); setCalibrationImages((current) => ({ ...current, [slot]: null })); }} /> : <span className="chest-calibration-selected-name">已标记物品</span>}<input inputMode="numeric" pattern="[0-9]*" value={calibrationDrafts[slot] ?? ""} placeholder="数量" onChange={(input) => setCalibrationDrafts((current) => ({ ...current, [slot]: input.target.value.replace(/[^0-9]/g, "") }))} /><button className="icon-button danger" title={`删除第 ${slot} 个物品`} onClick={() => removeCalibrationItem(slot)}><Trash2 size={15} /></button></div>; }) : <p className="empty-note padded-note">该截图暂未识别出物品，请新增校准物品。</p>}<button className="button secondary calibration-add-item" onClick={addCalibrationItem}><Plus size={15} />新增物品</button></div></div><footer><span>可选择已保存来源，也可新增自定义来源；保存后会写入本次记录。</span><button className="button secondary" onClick={() => setCalibrationEvent(null)}>取消</button><button className="button primary" disabled={savingCalibration} onClick={saveCalibration}>{savingCalibration ? "保存中" : "保存校准"}</button></footer></section></div> : null}
  </div>;
}

function Metric({ label, value, detail }: { label: string; value: unknown; detail?: string }) {
  return <div><span>{label}</span><strong>{String(value ?? 0)}</strong>{detail ? <small>{detail}</small> : null}</div>;
}
