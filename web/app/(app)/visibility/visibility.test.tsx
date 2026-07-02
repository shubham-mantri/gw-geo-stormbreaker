import { screen, fireEvent } from "@testing-library/react";
import { describe, it, expect } from "vitest";

import VisibilityPage from "./page";
import { renderWithClient, mockApi } from "@/test/utils";

describe("VisibilityPage", () => {
  it("shows per-engine confidence intervals", async () => {
    mockApi({
      visibility: {
        engines: [
          {
            engine: "chatgpt",
            mention_rate: 0.42,
            ci: [0.36, 0.48],
            cited: 0.31,
            avg_position: 2.4,
            sentiment: "positive",
            n_samples: 120,
            trend: [],
          },
        ],
        prompts: [],
      },
    });
    renderWithClient(<VisibilityPage />);

    expect(await screen.findByText(/chatgpt/i)).toBeInTheDocument();
    expect(await screen.findByText(/n=120/)).toBeInTheDocument(); // ConfidenceBadge
  });

  it("expands a prompt row to reveal sampled-answer counts", async () => {
    mockApi({
      visibility: {
        engines: [
          {
            engine: "chatgpt",
            mention_rate: 0.42,
            ci: [0.36, 0.48],
            cited: 0.31,
            avg_position: 2.4,
            sentiment: "positive",
            n_samples: 120,
            trend: [],
          },
        ],
        prompts: [
          {
            prompt_id: "p1",
            text: "best CRM for startups",
            mention_rate: 0.5,
            avg_position: 2.0,
            n_samples: 24,
          },
        ],
      },
    });
    renderWithClient(<VisibilityPage />);

    const row = await screen.findByRole("button", {
      name: /best CRM for startups/i,
    });
    fireEvent.click(row);
    expect(await screen.findByText(/24 sampled answers/i)).toBeInTheDocument();
  });
});
