export type DatabaseInfo = {
  path: string
  name: string
  size: number
  modified_at: string
}

export type TableInfo = {
  name: string
  label: string
  count: number
  columns: string[]
}

export type DatabaseOverview = DatabaseInfo & { tables: TableInfo[] }

export type TableRows = {
  table: string
  columns: string[]
  rows: Record<string, unknown>[]
  total: number
  page: number
  page_size: number
  pages: number
}

export type BundleInfo = {
  path: string
  family_id: string
  parser_revision?: number
  normalization_revision?: number
  mapping_revision?: number
  taxonomy?: string
  evidence_count?: number
  golden_path?: string
  error?: string
}

export type ReportInfo = {
  path: string
  name: string
  title: string
  research_id?: string
  size: number
}
