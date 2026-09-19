export default function SettingsPage() {
  return (
    <div>
      <h1 className="text-xl font-semibold text-neutral-900 dark:text-white">Settings</h1>
      <p className="mt-4 text-sm text-neutral-600 dark:text-neutral-300">
        Business config (services, price list, hours, persona) is written during onboarding
        and read by the agents directly — wire this page to a save-config endpoint once one
        is exposed for tenant self-service; today `save_config` exists in persistence but
        isn&apos;t exposed as an API route in what we inspected.
      </p>
    </div>
  );
}
