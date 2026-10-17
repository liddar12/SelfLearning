// Separate opt-in additive fixture; existing migration/bridge tests unchanged.
import { readFile } from 'node:fs/promises';
import { openObservedDatabase } from './harness.mjs';

export const deliveryMigration = new URL('../../supabase/migrations/20261016033201_capture_delivery_ledger_v1.sql', import.meta.url);
export async function openCaptureDeliveryDatabase() {
  const db = await openObservedDatabase();
  try {
    await db.exec(await readFile(deliveryMigration, 'utf8'));
    return db;
  } catch (error) {
    await db.close();
    throw error;
  }
}
