import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import axios from 'axios';

interface Comment {
  text: string;
  username: string;
  source: string;
  is_hate: boolean;
  confidence?: number;
  categories?: string[];
  explanation?: string;
}

interface ReviewData {
  scan_id: string;
  pending_comments: Comment[];
  total_count: number;
}

const CommentReview = () => {
  const { scanId } = useParams<{ scanId: string }>();
  const navigate = useNavigate();
  const [reviewData, setReviewData] = useState<ReviewData | null>(null);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [processing, setProcessing] = useState(false);
  const [approvedCount, setApprovedCount] = useState(0);
  const [rejectedCount, setRejectedCount] = useState(0);

  useEffect(() => {
    if (!scanId) {
      setError('No scan ID provided');
      return;
    }

    const fetchReviewData = async () => {
      try {
        const response = await axios.get<ReviewData>(`http://localhost:8000/api/scan/${scanId}/pending-review`);
        setReviewData(response.data);
        setLoading(false);
      } catch (err: any) {
        setError(err.response?.data?.detail || 'Failed to load review data');
        setLoading(false);
      }
    };

    fetchReviewData();
  }, [scanId]);

  const handleDecision = async (approved: boolean) => {
    if (!reviewData || processing) return;

    setProcessing(true);
    try {
      await axios.post(`http://localhost:8000/api/scan/${scanId}/review-comment`, {
        comment_index: currentIndex,
        approved: approved
      });

      if (approved) {
        setApprovedCount(prev => prev + 1);
      } else {
        setRejectedCount(prev => prev + 1);
      }

      // Move to next comment or complete review
      if (currentIndex < reviewData.pending_comments.length - 1) {
        setCurrentIndex(prev => prev + 1);
      } else {
        // All comments reviewed, complete the process
        await axios.post(`http://localhost:8000/api/scan/${scanId}/complete-review`);
        navigate(`/scan-status`, { 
          state: { 
            scanId: scanId,
            reviewComplete: true,
            approvedCount: approved ? approvedCount + 1 : approvedCount,
            rejectedCount: approved ? rejectedCount : rejectedCount + 1
          }
        });
      }
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to process decision');
    } finally {
      setProcessing(false);
    }
  };

  if (loading) {
    return <div className="flex justify-center items-center h-64">Loading review data...</div>;
  }

  if (error) {
    return <div className="text-red-500 text-center p-4">{error}</div>;
  }

  if (!reviewData || reviewData.pending_comments.length === 0) {
    return <div className="text-center p-4">No comments to review</div>;
  }

  const currentComment = reviewData.pending_comments[currentIndex];
  const progress = ((currentIndex) / reviewData.pending_comments.length) * 100;

  return (
    <div className="max-w-4xl mx-auto p-6">
      <div className="bg-white shadow rounded-lg">
        <div className="px-6 py-4 border-b border-gray-200">
          <h1 className="text-2xl font-bold text-gray-900">Comment Review</h1>
          <div className="mt-2 flex justify-between items-center">
            <span className="text-sm text-gray-600">
              Comment {currentIndex + 1} of {reviewData.pending_comments.length}
            </span>
            <span className="text-sm text-gray-600">
              Approved: {approvedCount} | Rejected: {rejectedCount}
            </span>
          </div>
          
          {/* Progress bar */}
          <div className="mt-3 w-full bg-gray-200 rounded-full h-2">
            <div 
              className="bg-blue-600 h-2 rounded-full transition-all duration-300" 
              style={{ width: `${progress}%` }}
            ></div>
          </div>
        </div>

        <div className="p-6">
          {/* Comment Details */}
          <div className="mb-6">
            <div className="flex items-center mb-4">
              <div className="w-12 h-12 bg-gray-300 rounded-full flex items-center justify-center">
                <span className="text-lg font-medium text-gray-700">
                  {currentComment.username?.charAt(0)?.toUpperCase() || 'U'}
                </span>
              </div>
              <div className="ml-4">
                <h3 className="text-lg font-medium text-gray-900">{currentComment.username || 'Unknown User'}</h3>
                <p className="text-sm text-gray-500">Source: {currentComment.source}</p>
              </div>
            </div>

            {/* Comment Content */}
            <div className="bg-gray-50 rounded-lg p-4 mb-4">
              <h4 className="text-sm font-medium text-gray-700 mb-2">Comment Text:</h4>
              <p className="text-gray-900 whitespace-pre-wrap">{currentComment.text}</p>
            </div>

            {/* AI Analysis */}
            <div className="bg-red-50 border border-red-200 rounded-lg p-4">
              <h4 className="text-sm font-medium text-red-800 mb-2">AI Analysis:</h4>
              <div className="space-y-2">
                <p className="text-sm text-red-700">
                  <strong>Confidence:</strong> {currentComment.confidence ? (currentComment.confidence * 100).toFixed(1) + '%' : 'N/A'}
                </p>
                {currentComment.categories && currentComment.categories.length > 0 && (
                  <p className="text-sm text-red-700">
                    <strong>Categories:</strong> {currentComment.categories.join(', ')}
                  </p>
                )}
                <p className="text-sm text-red-700">
                  <strong>Explanation:</strong> {currentComment.explanation || 'No explanation provided'}
                </p>
              </div>
            </div>
          </div>

          {/* Decision Buttons */}
          <div className="flex space-x-4">
            <button
              onClick={() => handleDecision(false)}
              disabled={processing}
              className="flex-1 bg-gray-600 text-white px-6 py-3 rounded-lg hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-gray-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {processing ? 'Processing...' : 'Reject - No Evidence'}
            </button>
            <button
              onClick={() => handleDecision(true)}
              disabled={processing}
              className="flex-1 bg-red-600 text-white px-6 py-3 rounded-lg hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-red-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {processing ? 'Processing...' : 'Approve - Create Evidence'}
            </button>
          </div>

          {/* Help text */}
          <div className="mt-6 bg-blue-50 border border-blue-200 rounded-lg p-4">
            <p className="text-sm text-blue-800">
              <strong>Review carefully:</strong> Approving will create permanent evidence files that can be used in legal proceedings. 
              Only approve if you believe this content violates community standards or laws.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default CommentReview;



