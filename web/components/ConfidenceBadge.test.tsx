import { render, screen } from "@testing-library/react";
import { ConfidenceBadge } from "./ConfidenceBadge";
it("shows CI and sample size", () => {
  render(<ConfidenceBadge value={0.42} ci={[0.36, 0.48]} n={120} />);
  expect(screen.getByText(/42%/)).toBeInTheDocument();
  expect(screen.getByText(/±/)).toBeInTheDocument();
  expect(screen.getByText(/n=120/)).toBeInTheDocument();
});
