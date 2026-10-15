import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Flame, Gauge, Loader2, Pencil, Plus, Radio, Search, ShieldAlert, Timer, Trash2, Wind, X } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { api } from '../api/client';
import type { ExternalChamberEntity } from '../api/client';
import { useToast } from '../contexts/ToastContext';
import { Button } from './Button';
import { Card, CardContent, CardHeader } from './Card';
import { Toggle } from './Toggle';

const fieldClass =
  'w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-sm text-white focus:outline-none focus:border-bambu-green disabled:cursor-not-allowed disabled:opacity-50';

const initialForm = () => ({
  printer_id: 0,
  name: '',
  climate_entity_id: '',
  duration_entity_id: '',
  heater_ready_entity_id: '',
  ventilation_ready_entity_id: '',
  ventilation_fan_entity_id: '',
  enabled: true,
  strict: false,
  manual_duration_minutes: 240,
  cooling_fan_percentage: 70,
});

interface EntityPickerProps {
  label: string;
  icon: LucideIcon;
  domain: ExternalChamberEntity['domain'];
  entities: ExternalChamberEntity[];
  value: string;
  loading: boolean;
  disabled?: boolean;
  onChange: (value: string) => void;
}

function EntityPicker({ label, icon: Icon, domain, entities, value, loading, disabled = false, onChange }: EntityPickerProps) {
  const { t } = useTranslation();
  const pickerRef = useRef<HTMLDivElement>(null);
  const optionsId = useId();
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const selectedEntity = entities.find((entity) => entity.entity_id === value);
  const normalizedSearch = search.trim().toLowerCase();
  const filteredEntities = entities.filter(
    (entity) =>
      entity.domain === domain &&
      (!normalizedSearch ||
        entity.friendly_name.toLowerCase().includes(normalizedSearch) ||
        entity.entity_id.toLowerCase().includes(normalizedSearch))
  );

  useEffect(() => {
    const closeOnOutsideClick = (event: MouseEvent) => {
      if (!pickerRef.current?.contains(event.target as Node)) {
        setOpen(false);
        setSearch('');
      }
    };

    document.addEventListener('mousedown', closeOnOutsideClick);
    return () => document.removeEventListener('mousedown', closeOnOutsideClick);
  }, []);

  return (
    <div ref={pickerRef} className="relative">
      <label className="mb-1 flex items-center gap-1.5 text-sm text-bambu-gray">
        <Icon className="h-3.5 w-3.5" />
        {label}
      </label>
      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-bambu-gray" />
        <input
          type="text"
          role="combobox"
          aria-expanded={open}
          aria-controls={optionsId}
          value={open ? search : selectedEntity ? `${selectedEntity.friendly_name} (${selectedEntity.entity_id})` : value}
          disabled={loading || disabled}
          placeholder={t('smartPlugs.addSmartPlug.placeholders.searchEntities')}
          className={`${fieldClass} pl-9 pr-9 placeholder:text-bambu-gray`}
          onChange={(event) => {
            setSearch(event.target.value);
            setOpen(true);
          }}
          onFocus={() => {
            setSearch('');
            setOpen(true);
          }}
          onKeyDown={(event) => {
            if (event.key === 'Escape') {
              setOpen(false);
              setSearch('');
              event.currentTarget.blur();
            }
          }}
        />
        {loading ? (
          <Loader2 className="absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-bambu-gray" />
        ) : value && !open && !disabled ? (
          <button
            type="button"
            aria-label={t('common.clear')}
            title={t('common.clear')}
            className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-bambu-gray transition-colors hover:bg-bambu-dark-tertiary hover:text-white"
            onClick={() => {
              onChange('');
              setSearch('');
            }}
          >
            <X className="h-4 w-4" />
          </button>
        ) : null}
      </div>

      {open && !loading && (
        <div
          id={optionsId}
          role="listbox"
          className="absolute z-50 mt-1 max-h-60 w-full overflow-y-auto rounded-lg border border-bambu-dark-tertiary bg-bambu-dark shadow-lg"
        >
          {filteredEntities.length ? (
            filteredEntities.map((entity) => (
              <button
                key={entity.entity_id}
                type="button"
                role="option"
                aria-selected={entity.entity_id === value}
                className={`w-full px-3 py-2 text-left text-sm transition-colors hover:bg-bambu-dark-tertiary ${
                  entity.entity_id === value ? 'bg-bambu-green/20 text-bambu-green' : 'text-white'
                }`}
                onClick={() => {
                  onChange(entity.entity_id);
                  setOpen(false);
                  setSearch('');
                }}
              >
                <div className="font-medium">{entity.friendly_name}</div>
                <div className="flex items-center justify-between gap-3 text-xs text-bambu-gray">
                  <span className="truncate">{entity.entity_id}</span>
                  {entity.state !== null && <span className="shrink-0">{entity.state}</span>}
                </div>
              </button>
            ))
          ) : (
            <div className="px-3 py-2 text-sm text-bambu-gray">
              {normalizedSearch
                ? t('smartPlugs.noEntitiesMatching', { search: search.trim() })
                : t('smartPlugs.noEntitiesAvailable')}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function ExternalChamberSettings() {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState(initialForm);

  const bindings = useQuery({
    queryKey: ['ha-chamber-heaters'],
    queryFn: () => api.getExternalChamberBindings(),
  });
  const printers = useQuery({ queryKey: ['printers'], queryFn: api.getPrinters });
  const entities = useQuery({
    queryKey: ['ha-chamber-heater-entities'],
    queryFn: api.getExternalChamberEntities,
    enabled: showForm,
  });

  const availablePrinters = useMemo(
    () =>
      (printers.data || []).filter(
        (printer) =>
          !printer.supports_chamber_heater &&
          !(bindings.data || []).some((binding) => binding.printer_id === printer.id && binding.id !== editingId)
      ),
    [printers.data, bindings.data, editingId]
  );

  const create = useMutation({
    mutationFn: () => api.createExternalChamberBinding({
      ...form,
      ventilation_fan_entity_id: form.ventilation_fan_entity_id || null,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ha-chamber-heaters'] });
      setForm(initialForm());
      setShowForm(false);
      showToast(t('externalChamber.saved'), 'success');
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });
  const update = useMutation({
    mutationFn: ({ id, data }: { id: number; data: Parameters<typeof api.updateExternalChamberBinding>[1] }) =>
      api.updateExternalChamberBinding(id, data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['ha-chamber-heaters'] }),
    onError: (error: Error) => showToast(error.message, 'error'),
  });
  const remove = useMutation({
    mutationFn: api.deleteExternalChamberBinding,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['ha-chamber-heaters'] }),
    onError: (error: Error) => showToast(error.message, 'error'),
  });

  const closeForm = () => {
    setShowForm(false);
    setEditingId(null);
    setForm(initialForm());
  };
  const openCreate = () => {
    setEditingId(null);
    setForm(initialForm());
    setShowForm(true);
  };
  const openEdit = (binding: NonNullable<typeof bindings.data>[number]) => {
    setEditingId(binding.id);
    setForm({
      printer_id: binding.printer_id,
      name: binding.name,
      climate_entity_id: binding.climate_entity_id,
      duration_entity_id: binding.duration_entity_id,
      heater_ready_entity_id: binding.heater_ready_entity_id,
      ventilation_ready_entity_id: binding.ventilation_ready_entity_id || '',
      ventilation_fan_entity_id: binding.ventilation_fan_entity_id || '',
      enabled: binding.enabled,
      strict: binding.strict,
      manual_duration_minutes: binding.manual_duration_minutes,
      cooling_fan_percentage: binding.cooling_fan_percentage,
    });
    setShowForm(true);
  };
  const editingBinding = bindings.data?.find((binding) => binding.id === editingId);
  const mappingsLocked = editingBinding?.owner_queue_item_id != null;
  const canSave =
    form.printer_id > 0 &&
    form.name.trim() !== '' &&
    form.climate_entity_id !== '' &&
    form.duration_entity_id !== '' &&
    form.heater_ready_entity_id !== '' &&
    form.ventilation_ready_entity_id !== '';

  return (
    <section className="mt-8" id="card-external-chambers">
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold text-white">
            <Flame className="h-5 w-5 text-bambu-green" />
            {t('externalChamber.title')}
          </h2>
          <p className="mt-1 text-sm text-bambu-gray">{t('externalChamber.description')}</p>
        </div>
        <Button
          className="self-start whitespace-nowrap"
          size="sm"
          onClick={() => (showForm ? closeForm() : openCreate())}
        >
          <Plus className={`h-4 w-4 transition-transform ${showForm ? 'rotate-45' : ''}`} />
          {showForm ? t('common.cancel') : t('externalChamber.add')}
        </Button>
      </div>

      {showForm && (
        <Card className="mb-4">
          <CardHeader>
            <div className="flex items-center gap-3">
              <div className="rounded-full bg-bambu-green/20 p-2 text-bambu-green">
                <Flame className="h-5 w-5" />
              </div>
              <div>
                <h3 className="font-semibold text-white">
                  {editingId ? t('common.edit') : t('externalChamber.add')}
                </h3>
                <p className="text-xs text-bambu-gray">{t('externalChamber.description')}</p>
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-5">
            {entities.error && (
              <div className="rounded-lg border border-red-300 bg-red-100 p-3 text-sm text-red-700 dark:border-red-500/50 dark:bg-red-500/20 dark:text-red-400">
                {(entities.error as Error).message}
              </div>
            )}

            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <div>
                <label className="mb-1 block text-sm text-bambu-gray">{t('common.name')}</label>
                <input
                  className={fieldClass}
                  value={form.name}
                  onChange={(event) => setForm({ ...form, name: event.target.value })}
                />
              </div>
              <div>
                <label className="mb-1 block text-sm text-bambu-gray">{t('externalChamber.selectPrinter')}</label>
                <select
                  className={fieldClass}
                  value={form.printer_id}
                  disabled={mappingsLocked}
                  onChange={(event) => setForm({ ...form, printer_id: Number(event.target.value) })}
                >
                  <option value={0}>{t('externalChamber.selectPrinter')}</option>
                  {availablePrinters.map((printer) => (
                    <option key={printer.id} value={printer.id}>{printer.name}</option>
                  ))}
                </select>
              </div>
              <EntityPicker
                label={t('externalChamber.selectClimate')}
                icon={Gauge}
                domain="climate"
                entities={entities.data || []}
                value={form.climate_entity_id}
                loading={entities.isLoading}
                disabled={mappingsLocked}
                onChange={(climate_entity_id) => setForm({ ...form, climate_entity_id })}
              />
              <EntityPicker
                label={t('externalChamber.selectDuration')}
                icon={Timer}
                domain="number"
                entities={entities.data || []}
                value={form.duration_entity_id}
                loading={entities.isLoading}
                disabled={mappingsLocked}
                onChange={(duration_entity_id) => setForm({ ...form, duration_entity_id })}
              />
              <div className="md:col-span-2">
                <EntityPicker
                  label={t('externalChamber.selectHeaterReady')}
                  icon={Radio}
                  domain="binary_sensor"
                  entities={entities.data || []}
                  value={form.heater_ready_entity_id}
                  loading={entities.isLoading}
                  disabled={mappingsLocked}
                  onChange={(heater_ready_entity_id) => setForm({ ...form, heater_ready_entity_id })}
                />
              </div>
              <div>
                <EntityPicker
                  label={t('externalChamberCooling.selectVentilationReady')}
                  icon={Wind}
                  domain="binary_sensor"
                  entities={entities.data || []}
                  value={form.ventilation_ready_entity_id}
                  loading={entities.isLoading}
                  disabled={mappingsLocked}
                  onChange={(ventilation_ready_entity_id) => setForm({ ...form, ventilation_ready_entity_id })}
                />
              </div>
              <div>
                <EntityPicker
                  label={t('externalChamberCooling.selectVentilationFan')}
                  icon={Wind}
                  domain="fan"
                  entities={entities.data || []}
                  value={form.ventilation_fan_entity_id}
                  loading={entities.isLoading}
                  disabled={mappingsLocked}
                  onChange={(ventilation_fan_entity_id) => setForm({ ...form, ventilation_fan_entity_id })}
                />
              </div>
            </div>

            <div className="grid grid-cols-1 gap-4 rounded-lg bg-bambu-dark p-4 md:grid-cols-2">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="text-sm text-white">{t('externalChamber.strict')}</p>
                  <p className="text-xs text-bambu-gray">{t('externalChamber.strictDescription')}</p>
                </div>
                <Toggle checked={form.strict} onChange={(strict) => setForm({ ...form, strict })} />
              </div>
              <div>
                <label className="mb-1 block text-sm text-bambu-gray">{t('externalChamber.manualDuration')}</label>
                <input
                  type="number"
                  min={15}
                  max={1440}
                  step={15}
                  className={fieldClass}
                  value={form.manual_duration_minutes}
                  onChange={(event) => setForm({ ...form, manual_duration_minutes: Number(event.target.value) })}
                />
              </div>
              <div>
                <label className="mb-1 block text-sm text-bambu-gray">
                  {t('externalChamberCooling.coolingFanPercentage')}
                </label>
                <input
                  type="number"
                  min={0}
                  max={100}
                  className={fieldClass}
                  value={form.cooling_fan_percentage}
                  disabled={!form.ventilation_fan_entity_id}
                  onChange={(event) => setForm({ ...form, cooling_fan_percentage: Number(event.target.value) })}
                />
              </div>
            </div>

            <div className="flex justify-end gap-2">
              <Button variant="secondary" onClick={closeForm}>{t('common.cancel')}</Button>
              <Button
                disabled={!canSave || create.isPending || update.isPending}
                onClick={() => {
                  if (editingId) {
                    update.mutate(
                      { id: editingId, data: { ...form, ventilation_fan_entity_id: form.ventilation_fan_entity_id || null } },
                      { onSuccess: closeForm },
                    );
                  } else {
                    create.mutate();
                  }
                }}
              >
                {(create.isPending || update.isPending) && <Loader2 className="h-4 w-4 animate-spin" />}
                {t('common.save')}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {bindings.isLoading ? (
        <div className="flex justify-center py-10">
          <Loader2 className="h-7 w-7 animate-spin text-bambu-green" />
        </div>
      ) : bindings.error ? (
        <Card><CardContent className="py-8 text-center text-sm text-red-400">{bindings.error.message}</CardContent></Card>
      ) : bindings.data?.length ? (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-3">
          {bindings.data.map((binding) => {
            const printer = printers.data?.find((candidate) => candidate.id === binding.printer_id);
            return (
              <Card key={binding.id}>
                <CardHeader>
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex min-w-0 items-center gap-3">
                      <div className="rounded-full bg-bambu-green/20 p-2 text-bambu-green">
                        <Flame className="h-4 w-4" />
                      </div>
                      <div className="min-w-0">
                        <h3 className="truncate font-semibold text-white">{binding.name}</h3>
                        <p className="truncate text-xs text-bambu-gray">{printer?.name}</p>
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-1">
                      <button
                        type="button"
                        aria-label={t('common.edit')}
                        title={t('common.edit')}
                        onClick={() => openEdit(binding)}
                        className="rounded-lg p-2 text-bambu-gray transition-colors hover:bg-bambu-green/10 hover:text-bambu-green"
                      >
                        <Pencil className="h-4 w-4" />
                      </button>
                      <button
                        type="button"
                        aria-label={t('common.delete')}
                        title={t('common.delete')}
                        onClick={() => remove.mutate(binding.id)}
                        className="rounded-lg p-2 text-bambu-gray transition-colors hover:bg-red-500/10 hover:text-red-400"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="space-y-2 rounded-lg bg-bambu-dark p-3 text-xs text-bambu-gray">
                    <div className="flex items-center gap-2">
                      <Gauge className="h-3.5 w-3.5 shrink-0" />
                      <span className="truncate" title={binding.climate_entity_id}>{binding.climate_entity_id}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <Timer className="h-3.5 w-3.5 shrink-0" />
                      <span className="truncate" title={binding.duration_entity_id}>{binding.duration_entity_id}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <Radio className="h-3.5 w-3.5 shrink-0" />
                      <span className="truncate" title={binding.heater_ready_entity_id}>
                        {binding.heater_ready_entity_id}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <Wind className="h-3.5 w-3.5 shrink-0" />
                      <span className="truncate" title={binding.ventilation_ready_entity_id || undefined}>
                        {binding.ventilation_ready_entity_id || t('externalChamberCooling.coolingNotConfigured')}
                      </span>
                    </div>
                    {binding.ventilation_fan_entity_id && (
                      <div className="flex items-center gap-2">
                        <Wind className="h-3.5 w-3.5 shrink-0" />
                        <span className="truncate" title={binding.ventilation_fan_entity_id}>
                          {binding.ventilation_fan_entity_id} · {binding.cooling_fan_percentage}%
                        </span>
                      </div>
                    )}
                  </div>
                  <div className="flex items-center justify-between gap-4">
                    <span className="text-sm text-white">{t('common.enable')}</span>
                    <Toggle
                      checked={binding.enabled}
                      disabled={update.isPending}
                      onChange={(enabled) => update.mutate({ id: binding.id, data: { enabled } })}
                    />
                  </div>
                  <div className="flex items-center justify-between gap-4">
                    <div className="flex items-center gap-2">
                      <ShieldAlert className="h-4 w-4 text-bambu-gray" />
                      <span className="text-sm text-white">{t('externalChamber.strict')}</span>
                    </div>
                    <Toggle
                      checked={binding.strict}
                      disabled={update.isPending}
                      onChange={(strict) => update.mutate({ id: binding.id, data: { strict } })}
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-sm text-bambu-gray">{t('externalChamber.manualDuration')}</label>
                    <input
                      key={binding.manual_duration_minutes}
                      type="number"
                      min={15}
                      max={1440}
                      step={15}
                      defaultValue={binding.manual_duration_minutes}
                      className={fieldClass}
                      onBlur={(event) => {
                        const value = Number(event.target.value);
                        if (value !== binding.manual_duration_minutes) {
                          update.mutate({ id: binding.id, data: { manual_duration_minutes: value } });
                        }
                      }}
                    />
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      ) : (
        <Card>
          <CardContent className="py-10">
            <div className="text-center text-bambu-gray">
              <Flame className="mx-auto mb-3 h-12 w-12 opacity-30" />
              <p className="text-sm">{t('externalChamber.empty')}</p>
            </div>
          </CardContent>
        </Card>
      )}
    </section>
  );
}
