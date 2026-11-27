export const fieldLabels: Record<string, string> = {
  __rowid__: 'ID строки',
  id: 'ID',
  family_id: 'Family',
  revision: 'Ревизия',
  parser_revision: 'Ревизия парсера',
  normalization_revision: 'Ревизия нормализации',
  mapping_revision: 'Ревизия mapping',
  created_at: 'Создано',
  name: 'Название',
  definition: 'Определение',
  description: 'Описание',
  evidence: 'Примеры / evidence',
  patterns: 'Grok-паттерны',
  layers: 'Слои разбора',
  stages: 'Этапы pipeline',
  record_schema: 'Схема записи',
  constraints: 'Ограничения',
  family_description: 'Описание Family',
  parser_description: 'Описание парсера',
  response: 'Ответ модели',
  fields: 'Поля',
  mappings: 'Правила mapping',
  taxonomy: 'Таксономия',
  source_timezone: 'Часовой пояс',
  field_type: 'Тип поля',
  type: 'Тип',
  required: 'Обязательное',
  automatic: 'Автоматическое',
  value: 'Значение',
  generic: 'Общая категория',
  high: 'Высокий уровень',
  low: 'Низкий уровень',
  entity_type: 'Тип сущности',
  entity_id: 'ID сущности',
  content: 'Содержимое',
  model_name: 'Модель',
  dimension: 'Размерность',
  vector: 'Вектор',
  positive_examples: 'Положительные примеры',
  negative_examples: 'Отрицательные примеры',
  essence: 'Суть события',
  structure: 'Структура',
  strategy: 'Стратегия',
  parser: 'Парсер',
  normalization: 'Нормализация',
  mapping: 'Mapping',
  dispatch: 'Диспетчеризация',
  raw: 'RAW',
  source: 'Источник',
  category: 'Категория',
}

export function fieldLabel(key: string) {
  return fieldLabels[key] || key.replaceAll('_', ' ')
}

export function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} Б`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} КБ`
  return `${(bytes / 1024 ** 2).toFixed(1)} МБ`
}

export function formatDate(value: string) {
  return new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

export function isPrimitive(value: unknown) {
  return value === null || ['string', 'number', 'boolean', 'undefined'].includes(typeof value)
}

export function cellText(value: unknown) {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? 'Да' : 'Нет'
  if (Array.isArray(value)) return value.length ? `${value.length} элем.` : 'Пусто'
  if (typeof value === 'object') {
    const entries = Object.entries(value as object)
    if (!entries.length) return 'Пусто'
    return entries.slice(0, 2).map(([key, item]) => `${fieldLabel(key)}: ${isPrimitive(item) ? String(item) : '…'}`).join(' · ')
  }
  return String(value)
}
