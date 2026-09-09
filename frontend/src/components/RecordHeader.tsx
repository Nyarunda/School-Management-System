import { ReactNode } from "react";
import { ActionIcon, Avatar, Box, Group, Stack, Text, Title, UnstyledButton } from "@mantine/core";
import { IconArrowLeft, IconChevronLeft, IconChevronRight } from "@tabler/icons-react";
import { StatusBadge } from "./ui";

// Actions/prev-next are optional on purpose -- most records this is used on today have no
// wired record-level mutation or adjacent-record navigation yet. Pass them once a real
// endpoint/data flow backs them; don't populate with buttons that go nowhere.
export function RecordHeader({ backLabel, onBack, initials, eyebrow, title, subtitle, status, actions, onPrevious, onNext }: {
  backLabel: string; onBack: () => void; initials: string; eyebrow?: string; title: string; subtitle?: ReactNode;
  status?: string; actions?: ReactNode; onPrevious?: () => void; onNext?: () => void;
}) {
  return (
    <Box mb="md">
      <UnstyledButton onClick={onBack} c="dimmed" mb="xs" style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13 }}>
        <IconArrowLeft size={15} />{backLabel}
      </UnstyledButton>
      <Group justify="space-between" align="flex-start" wrap="nowrap">
        <Group wrap="nowrap" gap="sm" style={{ minWidth: 0, flex: 1 }}>
          <Avatar radius="md" size={48} color="indigo" style={{ flexShrink: 0 }}>{initials}</Avatar>
          <Stack gap={0} style={{ minWidth: 0 }}>
            {eyebrow && <Text fz={11} fw={600} tt="uppercase" c="dimmed" style={{ letterSpacing: "0.04em" }}>{eyebrow}</Text>}
            <Title order={2} fz={21} fw={600} style={{ overflowWrap: "break-word" }}>{title}</Title>
            {subtitle && <Text fz={13} c="dimmed">{subtitle}</Text>}
          </Stack>
        </Group>
        <Group gap="sm" wrap="nowrap" style={{ flexShrink: 0 }}>
          {status && <StatusBadge value={status} />}
          {(onPrevious || onNext) && (
            <Group gap={4}>
              <ActionIcon variant="default" disabled={!onPrevious} onClick={onPrevious} aria-label="Previous record"><IconChevronLeft size={16} /></ActionIcon>
              <ActionIcon variant="default" disabled={!onNext} onClick={onNext} aria-label="Next record"><IconChevronRight size={16} /></ActionIcon>
            </Group>
          )}
        </Group>
      </Group>
      {actions && <Group gap="xs" mt="sm">{actions}</Group>}
    </Box>
  );
}
