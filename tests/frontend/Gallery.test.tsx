import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";

import { renderWithProviders } from "./test-utils";
import { Gallery } from "../../frontend/src/pages/Gallery";

const SAMPLE_PHOTO = {
  id: "p1",
  filename: "ph_1.jpg",
  captured_at: "2026-05-19T12:00:00Z",
  size_bytes: 8_400_000,
  width: 6000,
  height: 4000,
  iso: "200",
  shutter: "1/250",
  aperture: "f/8",
  starred: false,
  uploads: [
    {
      destination_id: "d1",
      destination_name: "Primary S3",
      state: "uploaded",
      uploaded_at: "2026-05-19T12:00:05Z",
      remote_key: "s3://bucket/ph_1.jpg",
    },
  ],
  path: "/media/sdcard/photos/2026/05/19/ph_1.jpg",
  thumb_url: "/api/v1/gallery/p1/thumb",
  original_url: "/api/v1/gallery/p1/original",
};

vi.mock("../../frontend/src/lib/bridge/gallery", () => ({
  gallery: {
    // The Gallery page paginates via listPage() (useInfiniteQuery), so the
    // mock mirrors that shape: one page holding the sample photo.
    listPage: vi.fn(async () => ({ items: [SAMPLE_PHOTO], total: 1 })),
    list: vi.fn(async () => [SAMPLE_PHOTO]),
    bulkDelete: vi.fn(async () => 0),
    star: vi.fn(async () => {}),
    retry: vi.fn(async () => {}),
    remove: vi.fn(async () => {}),
  },
}));

describe("Gallery", () => {
  it("renders photos and toolbar", async () => {
    renderWithProviders(<Gallery />);
    await waitFor(() => {
      expect(screen.getByText(/Gallery$/i)).toBeInTheDocument();
      expect(screen.getByText(/ph_1.jpg/)).toBeInTheDocument();
      expect(screen.getByText(/Synced/)).toBeInTheDocument();
    });
  });
});
